"""Low-level source request and hold changes, shared by matching (creating requests,
counting holds), shortage cancel and the request service. It imports no other service, so
`app.shortages.service` can use it without an import cycle.

Audit (business-rules.md §10, one row per state change): a request's rows go to the
requester's org, whose shortage the audit trail follows; a hold's rows go to the source
org, whose stock it sets aside."""

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.models import User
from app.domain.resolution import TRANSFER, TRANSFER_SPLIT
from app.domain.source_request import (
    ACTIVE_HOLD,
    HOLD_TRANSITIONS,
    OPEN_REQUEST,
    REQUEST_TRANSITIONS,
    HoldStatus,
    RequestStatus,
    response_deadline,
)
from app.domain.state_machine import transition
from app.shortages.models import MatchRun, Shortage
from app.source_requests.models import Hold, SourceRequest

REQUEST, HOLD = "source_request", "hold"


async def held_by_batch(
    session: AsyncSession,
    batch_ids: Iterable[uuid.UUID],
    *,
    exclude_shortage_id: uuid.UUID | None = None,
) -> dict[uuid.UUID, int]:
    """Active (TENTATIVE or FIRM) hold qty per batch, counted as `reserved` (§2).
    Matching passes its own shortage as `exclude_shortage_id`: holds count as reserved
    "for every other shortage"."""
    ids = list(batch_ids)
    if not ids:
        return {}
    stmt = (
        select(Hold.batch_id, func.sum(Hold.qty))
        .where(Hold.batch_id.in_(ids), Hold.status.in_(ACTIVE_HOLD))
        .group_by(Hold.batch_id)
    )
    if exclude_shortage_id is not None:
        stmt = stmt.join(SourceRequest, SourceRequest.id == Hold.source_request_id).where(
            SourceRequest.shortage_id != exclude_shortage_id
        )
    return {batch_id: int(qty) for batch_id, qty in await session.execute(stmt)}


async def open_requests(
    session: AsyncSession, shortage_id: uuid.UUID, *, lock: bool = False
) -> list[SourceRequest]:
    stmt = (
        select(SourceRequest)
        .where(SourceRequest.shortage_id == shortage_id, SourceRequest.status.in_(OPEN_REQUEST))
        .order_by(SourceRequest.id)
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return list(await session.scalars(stmt))


async def create_requests(
    session: AsyncSession, shortage: Shortage, run: MatchRun, now: datetime
) -> list[SourceRequest]:
    """One REQUESTED SourceRequest per planned source of a TRANSFER or TRANSFER_SPLIT run,
    due back by the response deadline (§6, §7 step 2). Other plans ask no one."""
    planned = run.planned_resolution
    if planned is None or planned["type"] not in (TRANSFER, TRANSFER_SPLIT):
        return []
    deadline = response_deadline(shortage.priority, now)
    created = []
    for line in planned["lines"]:
        sr = SourceRequest(
            id=uuid.uuid4(),
            shortage_id=shortage.id,
            candidate_id=uuid.UUID(line["candidate_id"]),
            source_org_id=uuid.UUID(line["source_org_id"]),
            qty=line["qty"],
            status=RequestStatus.REQUESTED,
            sla_deadline=deadline,
        )
        session.add(sr)
        created.append(sr)
    await session.flush()
    for sr in created:
        after = {
            "status": sr.status,
            "shortage_id": sr.shortage_id,
            "source_org_id": sr.source_org_id,
            "candidate_id": sr.candidate_id,
            "qty": sr.qty,
            "sla_deadline": sr.sla_deadline,
            "match_run_id": run.id,
        }
        await audit.record(
            session,
            None,
            REQUEST,
            sr.id,
            f"{REQUEST}.created",
            None,
            after,
            f"Match run {run.run_no} planned a {planned['type']} from this source.",
            org_id=shortage.org_id,
        )
        # S07: emit("source_request.created", source_request_id, shortage_id, product_id,
        #           qty, deadline) to the requester's and the source's orgs.
    return created


async def move_request(
    session: AsyncSession,
    shortage: Shortage,
    sr: SourceRequest,
    to: RequestStatus,
    actor: User | None,
    reason: str | None,
    **changes: Any,
) -> None:
    """Every request transition goes through here: state machine (409 if not allowed),
    any extra field `changes`, one audit row."""
    before = transition(sr, to, REQUEST_TRANSITIONS)
    for name, value in changes.items():
        setattr(sr, name, value)
    await session.flush()
    await session.refresh(sr)  # updated_at is set by the database
    await audit.record(
        session,
        actor,
        REQUEST,
        sr.id,
        f"{REQUEST}.status_changed",
        {"status": before},
        {"status": sr.status, **changes},
        reason,
        org_id=shortage.org_id,
    )
    # S07: emit("source_request.status_changed", source_request_id, from=before, to=sr.status)


async def add_hold(
    session: AsyncSession,
    sr: SourceRequest,
    batch_id: uuid.UUID,
    qty: int,
    expires_at: datetime,
    actor: User | None,
    reason: str | None,
) -> Hold:
    hold = Hold(
        source_request_id=sr.id,
        batch_id=batch_id,
        qty=qty,
        status=HoldStatus.TENTATIVE,
        expires_at=expires_at,
    )
    session.add(hold)
    await session.flush()
    after = {
        "status": hold.status,
        "source_request_id": sr.id,
        "batch_id": batch_id,
        "qty": qty,
        "expires_at": expires_at,
    }
    await audit.record(
        session, actor, HOLD, hold.id, f"{HOLD}.created", None, after, reason,
        org_id=sr.source_org_id,
    )  # fmt: skip
    return hold


async def move_hold(
    session: AsyncSession,
    hold: Hold,
    source_org_id: uuid.UUID,
    to: HoldStatus,
    actor: User | None,
    reason: str | None,
) -> None:
    before = transition(hold, to, HOLD_TRANSITIONS)
    await session.flush()
    await session.refresh(hold)
    await audit.record(
        session,
        actor,
        HOLD,
        hold.id,
        f"{HOLD}.status_changed",
        {"status": before},
        {"status": hold.status},
        reason,
        org_id=source_org_id,
    )


async def release(
    session: AsyncSession,
    shortage: Shortage,
    *,
    actor: User | None,
    reason: str | None,
    superseded_reason: str | None = None,
) -> tuple[list[Hold], list[SourceRequest]]:
    """Release every TENTATIVE hold of the shortage's requests and supersede every request
    still open. The one release path for every non-CONFIRMED exit: a decline or expiry
    (via `release_and_rematch`, after the caller has ended the request that caused it) and
    a shortage cancel. FIRM holds belong to CONFIRMED requests and are left alone."""
    rows: Sequence[tuple[Hold, uuid.UUID]] = (
        await session.execute(
            select(Hold, SourceRequest.source_org_id)
            .join(SourceRequest, SourceRequest.id == Hold.source_request_id)
            .where(
                SourceRequest.shortage_id == shortage.id,
                Hold.status == HoldStatus.TENTATIVE,
            )
            .order_by(Hold.batch_id, Hold.id)
            .with_for_update(of=Hold)
            .execution_options(populate_existing=True)
        )
    ).all()
    for hold, source_org_id in rows:
        await move_hold(session, hold, source_org_id, HoldStatus.RELEASED, actor, reason)
    superseded = await open_requests(session, shortage.id, lock=True)
    for sr in superseded:
        await move_request(
            session, shortage, sr, RequestStatus.SUPERSEDED, actor, superseded_reason or reason
        )
    return [h for h, _ in rows], superseded
