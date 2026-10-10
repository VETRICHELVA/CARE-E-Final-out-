"""Surplus posts (S18): create, withdraw, expire, and match to other orgs' demand.

A post offers one batch's excess, never more than the batch's current transferable
(CLAUDE.md rule 4). Matching runs when a post is created and nightly (app.worker): a live
post with something on offer is matched to every other org with an open shortage of the
product, or whose stored forecast runs out of the product within 14 days. Each new match is
a SurplusMatch row, moves an OPEN post to MATCHED and emits `surplus.matched` to both orgs.
A withdrawn or expired post is never matched.

Audit (business-rules.md §10): every post change is one row in the posting org. A match is
SYSTEM and names only the matched org (as `surplus.matched` does), not why it matched."""

import uuid
from collections.abc import Iterable, Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.models import User
from app.db import flush_or_conflict
from app.domain.events import EventType
from app.domain.inventory import batch_transferable
from app.domain.state_machine import transition
from app.domain.surplus import LIVE, TRANSITIONS, MatchKind, SurplusStatus, offered_qty
from app.errors import AppError
from app.events import service as events
from app.forecasting import service as forecasting
from app.inventory.models import InventoryBatch
from app.network.service import OPEN_DEMAND
from app.shortages.models import Shortage
from app.source_requests.holds import held_by_batch
from app.surplus.models import SurplusMatch, SurplusPost
from app.surplus.schemas import SurplusCreate

ENTITY = "surplus_post"
LIVE_POST = "A surplus post for this batch is already open."
EXPIRED = "The batch reached its expiry date."
MATCHED = "Matched to another hospital's demand for this product."


def _snapshot(post: SurplusPost) -> dict[str, object]:
    return {
        "batch_id": post.batch_id,
        "product_id": post.product_id,
        "qty": post.qty,
        "expiry_date": post.expiry_date,
        "min_price_paise": post.min_price_paise,
        "status": post.status,
    }


async def offered(
    session: AsyncSession, posts: Sequence[SurplusPost], day: date
) -> dict[uuid.UUID, int]:
    """What each post offers now: its qty up to its batch's transferable (net of holds)."""
    batch_ids = {p.batch_id for p in posts}
    if not batch_ids:
        return {}
    batches = {
        b.id: b
        for b in await session.scalars(
            select(InventoryBatch).where(InventoryBatch.id.in_(batch_ids))
        )
    }
    held = await held_by_batch(session, batch_ids)
    return {
        p.id: offered_qty(
            p.qty, batch_transferable(batches[p.batch_id], day, held.get(p.batch_id, 0))
        )
        for p in posts
    }


async def matched_org_ids(
    session: AsyncSession, post_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    ids = list(post_ids)
    out: dict[uuid.UUID, list[uuid.UUID]] = {i: [] for i in ids}
    if ids:
        stmt = (
            select(SurplusMatch.surplus_id, SurplusMatch.org_id)
            .where(SurplusMatch.surplus_id.in_(ids))
            .order_by(SurplusMatch.created_at, SurplusMatch.id)
        )
        for surplus_id, org_id in await session.execute(stmt):
            out[surplus_id].append(org_id)
    return out


async def create(session: AsyncSession, user: User, body: SurplusCreate, day: date) -> SurplusPost:
    """Post a batch of the caller's org; then match it. 403 for another org's batch, 400 for
    an expired batch or more than its transferable, 409 if the batch already has a live post."""
    batch = await session.get(InventoryBatch, body.batch_id, with_for_update=True)
    if batch is None:
        raise AppError(400, "validation", "Unknown batch.", {"batch_id": str(body.batch_id)})
    if batch.org_id != user.org_id:
        raise AppError(403, "forbidden", "This batch belongs to another organization.")
    if batch.expiry_date <= day:
        raise AppError(400, "validation", "This batch has expired.", {"reason": "expired"})
    held = await held_by_batch(session, [batch.id])
    transferable = batch_transferable(batch, day, held.get(batch.id, 0))
    if body.qty > transferable:
        raise AppError(
            400,
            "validation",
            f"Only {transferable} of this batch is transferable.",
            {"reason": "exceeds_transferable", "qty": body.qty, "transferable_qty": transferable},
        )
    post = SurplusPost(
        org_id=user.org_id,
        batch_id=batch.id,
        product_id=batch.product_id,
        qty=body.qty,
        expiry_date=batch.expiry_date,
        min_price_paise=body.min_price_paise,
        status=SurplusStatus.OPEN,
        created_by=user.id,
    )
    await flush_or_conflict(session, post, LIVE_POST)
    await audit.record(
        session, user, ENTITY, post.id, f"{ENTITY}.created", None, _snapshot(post), body.reason
    )
    await match(session, [post.id], day)
    return post


async def _locked(session: AsyncSession, post_id: uuid.UUID) -> SurplusPost | None:
    stmt = select(SurplusPost).where(SurplusPost.id == post_id).with_for_update()
    return await session.scalar(stmt.execution_options(populate_existing=True))


async def withdraw(
    session: AsyncSession, user: User, post_id: uuid.UUID, reason: str | None
) -> SurplusPost:
    post = await _locked(session, post_id)
    if post is None:
        raise AppError(404, "not_found", "Surplus post not found.")
    if post.org_id != user.org_id:
        raise AppError(403, "forbidden", "This surplus post belongs to another organization.")
    before = transition(post, SurplusStatus.WITHDRAWN, TRANSITIONS)
    await session.flush()
    await audit.record(
        session,
        user,
        ENTITY,
        post.id,
        f"{ENTITY}.withdrawn",
        {"status": before},
        {"status": post.status},
        reason,
    )
    return post


async def expire_due(session: AsyncSession, day: date) -> int:
    """Expire every live post whose batch expiry date has come; returns how many."""
    stmt = (
        select(SurplusPost)
        .where(SurplusPost.status.in_(LIVE), SurplusPost.expiry_date <= day)
        .order_by(SurplusPost.id)
        .with_for_update(skip_locked=True)
    )
    posts = list(await session.scalars(stmt))
    for post in posts:
        before = transition(post, SurplusStatus.EXPIRED, TRANSITIONS)
        await audit.record(
            session,
            None,
            ENTITY,
            post.id,
            f"{ENTITY}.expired",
            {"status": before},
            {"status": post.status},
            EXPIRED,
            org_id=post.org_id,
        )
    await session.flush()
    return len(posts)


async def _demand(
    session: AsyncSession, post: SurplusPost, day: date
) -> dict[uuid.UUID, tuple[MatchKind, uuid.UUID | None, date | None]]:
    """Other orgs' demand for the post's product: an open shortage (the oldest per org) or
    else a forecast stock-out within SURPLUS_STOCKOUT_MATCH_DAYS."""
    out: dict[uuid.UUID, tuple[MatchKind, uuid.UUID | None, date | None]] = {}
    shortages = await session.execute(
        select(Shortage.org_id, Shortage.id)
        .where(
            Shortage.product_id == post.product_id,
            Shortage.status.in_(OPEN_DEMAND),
            Shortage.org_id != post.org_id,
        )
        .order_by(Shortage.created_at, Shortage.id)
    )
    for org_id, shortage_id in shortages:
        out.setdefault(org_id, (MatchKind.SHORTAGE, shortage_id, None))
    stockouts = await forecasting.stockouts_within(session, post.product_id, day, post.org_id)
    for org_id, stockout in stockouts.items():
        out.setdefault(org_id, (MatchKind.FORECAST, None, stockout))
    return out


async def match(session: AsyncSession, post_ids: Iterable[uuid.UUID], day: date) -> int:
    """Match each live post with something on offer to orgs not matched yet; returns the
    number of new matches. A post another transaction holds is left for the next run."""
    total = 0
    for post_id in post_ids:
        post = await session.scalar(
            select(SurplusPost)
            .where(SurplusPost.id == post_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if post is None or post.status not in LIVE or post.expiry_date <= day:
            continue
        if (await offered(session, [post], day))[post.id] <= 0:
            continue
        already = set(
            await session.scalars(
                select(SurplusMatch.org_id).where(SurplusMatch.surplus_id == post.id)
            )
        )
        demand = await _demand(session, post, day)
        for org_id in sorted(set(demand) - already, key=str):
            kind, shortage_id, stockout = demand[org_id]
            session.add(
                SurplusMatch(
                    surplus_id=post.id,
                    org_id=org_id,
                    kind=kind,
                    shortage_id=shortage_id,
                    stockout_date=stockout,
                )
            )
            before = post.status
            if post.status == SurplusStatus.OPEN:
                transition(post, SurplusStatus.MATCHED, TRANSITIONS)
            await session.flush()
            await audit.record(
                session,
                None,
                ENTITY,
                post.id,
                f"{ENTITY}.matched",
                {"status": before},
                {"status": post.status, "matched_org_id": org_id},
                MATCHED,
                org_id=post.org_id,
            )
            await events.emit(
                session,
                EventType.SURPLUS_MATCHED,
                [post.org_id, org_id],
                {"surplus_id": post.id, "org_id": org_id},
            )
            total += 1
    return total


async def match_open(session: AsyncSession, day: date) -> int:
    """The nightly run: match every live post."""
    stmt = (
        select(SurplusPost.id)
        .where(SurplusPost.status.in_(LIVE), SurplusPost.expiry_date > day)
        .order_by(SurplusPost.created_at, SurplusPost.id)
    )
    return await match(session, list(await session.scalars(stmt)), day)
