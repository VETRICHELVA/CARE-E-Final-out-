"""Source requests: accept (row-locked tentative holds), decline, the deadline timer and
the release-and-rematch path (business-rules.md §2, §6, §7 steps 2, 3 and 6, §8, §10).

Lock order, everywhere: shortage, then source request, then batches by id. Accept, decline
and the timer all take the shortage row first, so they serialize per shortage and never
deadlock; competing accepts for the same batches serialize on the batch rows."""

import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.domain.inventory import batch_transferable, days_to_expiry_at
from app.domain.shortage import Status
from app.domain.source_request import (
    HOLD_DEADLINE_PASSED,
    RESPONSE_DEADLINE_PASSED,
    STOCK_CHANGED,
    HoldStatus,
    Lot,
    RequestStatus,
    all_ready,
    allocate,
    hold_deadline,
    is_overdue,
)
from app.domain.state_machine import InvalidTransition
from app.errors import AppError
from app.inventory.models import InventoryBatch
from app.shortages import service as shortages
from app.shortages.models import Candidate, MatchRun, Shortage, Trigger
from app.source_requests import holds
from app.source_requests.hooks import on_sources_ready
from app.source_requests.models import Hold, SourceRequest

log = logging.getLogger("app.source_requests")

DECLINED = "The source declined the request."
REMATCH_FROM = (Status.AWAITING_DECISION, Status.IN_FULFILLMENT)  # §8: back to MATCHING


class Settled(Exception):
    """The request ended while the call was handled (it was overdue, or stock ran out).
    The router commits that change, then answers with `error`."""

    def __init__(self, error: AppError) -> None:
        super().__init__(error.message)
        self.error = error


# --- reads -----------------------------------------------------------------------------------


async def _locked(session: AsyncSession, request_id: uuid.UUID) -> tuple[SourceRequest, Shortage]:
    """Lock the request's shortage, then the request (see the module lock order)."""
    sr = await session.get(SourceRequest, request_id)
    if sr is None:
        raise AppError(404, "not_found", "Source request not found.")
    shortage = await session.get_one(
        Shortage, sr.shortage_id, with_for_update=True, populate_existing=True
    )
    sr = await session.get_one(
        SourceRequest, request_id, with_for_update=True, populate_existing=True
    )
    return sr, shortage


async def _own_incoming(
    session: AsyncSession, user: User, request_id: uuid.UUID
) -> tuple[SourceRequest, Shortage]:
    """Only the source org may answer a request (403 for every other org)."""
    sr = await session.get(SourceRequest, request_id)
    if sr is None:
        raise AppError(404, "not_found", "Source request not found.")
    if sr.source_org_id != user.org_id:
        raise AppError(403, "forbidden", "Only the source organization can answer this request.")
    return await _locked(session, request_id)


async def _hold_expiry(session: AsyncSession, sr: SourceRequest) -> datetime | None:
    stmt = select(Hold.expires_at).where(
        Hold.source_request_id == sr.id, Hold.status == HoldStatus.TENTATIVE
    )
    return await session.scalar(stmt.order_by(Hold.expires_at).limit(1))


async def _deadline(session: AsyncSession, sr: SourceRequest) -> tuple[datetime | None, str]:
    """The deadline that applies to the request now, and the SYSTEM reason when it passes."""
    if sr.status == RequestStatus.REQUESTED:
        return sr.sla_deadline, RESPONSE_DEADLINE_PASSED
    if sr.status == RequestStatus.TENTATIVE_HOLD:
        return await _hold_expiry(session, sr), HOLD_DEADLINE_PASSED
    return None, ""


# --- the release path ------------------------------------------------------------------------


async def release_and_rematch(
    session: AsyncSession,
    shortage: Shortage,
    reason: str,
    *,
    trigger: Trigger,
    exclude: Iterable[uuid.UUID] = (),
    now: datetime,
) -> MatchRun | None:
    """§7 step 6, used by every non-CONFIRMED exit of a request: release every tentative
    hold of the shortage, supersede its other open requests, put the shortage back to
    MATCHING if it had moved on, and start a new match run without `exclude` (for this
    shortage; only a source that declined is excluded). `reason` is the factual SYSTEM
    cause. The caller has locked the shortage and already ended the request that caused
    this. Returns None if the shortage is closed."""
    await holds.release(
        session,
        shortage,
        hold_reason=reason,
        actor=None,
        reason=f"Another source request for this shortage ended: {reason}",
    )
    if shortage.status in REMATCH_FROM:
        await shortages.move_shortage(session, shortage, Status.MATCHING, None, reason)
    if shortage.status not in (Status.OPEN, Status.MATCHING):
        return None
    return await shortages.run_match(
        session, shortage, trigger, reason=reason, exclude=exclude, now=now
    )


async def _expire(
    session: AsyncSession, sr: SourceRequest, shortage: Shortage, reason: str, now: datetime
) -> None:
    """End a request by its deadline (or a stock change), then release and re-run. An
    expiry excludes no one (§7 step 6: declined -> excluded; expired -> not excluded), so a
    source that missed its response deadline can be asked again."""
    await holds.move_request(session, shortage, sr, RequestStatus.EXPIRED, None, reason)
    await release_and_rematch(session, shortage, reason, trigger=Trigger.EXPIRY, now=now)


async def _expire_if_overdue(
    session: AsyncSession, sr: SourceRequest, shortage: Shortage, to: RequestStatus, now: datetime
) -> None:
    """An answer that arrives after the response deadline expires the request instead, even
    if the timer has not run yet; the router commits that and returns 409."""
    deadline, reason = await _deadline(session, sr)
    if sr.status == RequestStatus.REQUESTED and deadline and is_overdue(deadline, now):
        await _expire(session, sr, shortage, reason, now)
        raise Settled(
            AppError(
                409,
                "invalid_transition",
                "The response deadline has passed, so this request has expired.",
                {"from": RequestStatus.EXPIRED, "to": to},
            )
        )


# --- answers ---------------------------------------------------------------------------------


async def accept(
    session: AsyncSession,
    user: User,
    request_id: uuid.UUID,
    reason: str | None,
    *,
    now: datetime | None = None,
) -> SourceRequest:
    """§7 step 3: in one transaction, lock the source's batches of the product, recompute
    transferable net of every active hold, place TENTATIVE holds earliest expiry first and
    move the request to TENTATIVE_HOLD. If the stock no longer covers the request: expire it
    ("Stock changed before acceptance."), re-run matching and raise `Settled` (409)."""
    now = now or datetime.now(UTC)
    sr, shortage = await _own_incoming(session, user, request_id)
    await _expire_if_overdue(session, sr, shortage, RequestStatus.TENTATIVE_HOLD, now)
    if sr.status != RequestStatus.REQUESTED:
        raise InvalidTransition(sr.status, RequestStatus.TENTATIVE_HOLD)

    candidate = await session.get_one(Candidate, sr.candidate_id)
    batches = list(
        await session.scalars(
            select(InventoryBatch)
            .where(
                InventoryBatch.org_id == sr.source_org_id,
                InventoryBatch.product_id == shortage.product_id,
            )
            .order_by(InventoryBatch.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    held = await holds.held_by_batch(session, (b.id for b in batches))  # after the lock
    today, arrival = now.date(), (now + timedelta(hours=candidate.eta_hours)).date()
    lots = [
        Lot(b.id, b.expiry_date, batch_transferable(b, today, held.get(b.id, 0)))
        for b in batches
        if days_to_expiry_at(b, arrival) >= shortage.min_shelf_life_days  # §2 shelf life
    ]
    picks = allocate(lots, sr.qty)
    if picks is None:
        available = sum(x.transferable for x in lots)
        await _expire(session, sr, shortage, STOCK_CHANGED, now)
        raise Settled(
            AppError(
                409,
                "conflict",
                STOCK_CHANGED,
                {"requested_qty": sr.qty, "transferable_qty": available},
            )
        )

    expires_at = hold_deadline(shortage.priority, now)
    for batch_id, qty in picks:
        await holds.add_hold(session, sr, batch_id, qty, expires_at, user, reason)
    await holds.move_request(
        session,
        shortage,
        sr,
        RequestStatus.TENTATIVE_HOLD,
        user,
        reason,
        responded_by=user.id,
        responded_at=now,
    )
    await _ready_check(session, shortage, sr)
    return sr


async def _ready_check(session: AsyncSession, shortage: Shortage, sr: SourceRequest) -> None:
    """§7 step 4: once every request of this run's plan holds stock, hand over to S09."""
    run_id = await session.scalar(
        select(Candidate.match_run_id).where(Candidate.id == sr.candidate_id)
    )
    statuses = await session.scalars(
        select(SourceRequest.status)
        .join(Candidate, Candidate.id == SourceRequest.candidate_id)
        .where(Candidate.match_run_id == run_id)
    )
    if all_ready(list(statuses)):
        await on_sources_ready(session, shortage, await session.get_one(MatchRun, run_id))


async def decline(
    session: AsyncSession,
    user: User,
    request_id: uuid.UUID,
    reason: str | None,
    *,
    now: datetime | None = None,
) -> SourceRequest:
    """REQUESTED -> DECLINED with an optional reason, then release and re-run without the
    declining org for this shortage (§7 step 6). No reason: SYSTEM, "No reason was entered."."""
    now = now or datetime.now(UTC)
    sr, shortage = await _own_incoming(session, user, request_id)
    await _expire_if_overdue(session, sr, shortage, RequestStatus.DECLINED, now)
    typed = (reason or "").strip() or None
    await holds.move_request(
        session,
        shortage,
        sr,
        RequestStatus.DECLINED,
        user,
        typed,
        responded_by=user.id,
        responded_at=now,
        decline_reason=typed,
        reason_source="USER" if typed else "SYSTEM",
    )
    await release_and_rematch(
        session, shortage, DECLINED, trigger=Trigger.DECLINE, exclude=[sr.source_org_id], now=now
    )
    return sr


# --- the timer -------------------------------------------------------------------------------


async def overdue_ids(session: AsyncSession, now: datetime) -> list[uuid.UUID]:
    """REQUESTED past the response deadline, and TENTATIVE_HOLD with a tentative hold past
    its deadline. Read from the database, so nothing is lost when a worker restarts."""
    requested = select(SourceRequest.id).where(
        SourceRequest.status == RequestStatus.REQUESTED, SourceRequest.sla_deadline <= now
    )
    holding = (
        select(SourceRequest.id)
        .join(Hold, Hold.source_request_id == SourceRequest.id)
        .where(
            SourceRequest.status == RequestStatus.TENTATIVE_HOLD,
            Hold.status == HoldStatus.TENTATIVE,
            Hold.expires_at <= now,
        )
    )
    return sorted(set(await session.scalars(requested.union(holding))))


async def expire_one(session: AsyncSession, request_id: uuid.UUID, now: datetime) -> bool:
    """Expire one overdue request under its locks. Re-checks after locking, so a second
    worker, or a run after a restart, finds nothing left to do and returns False."""
    sr, shortage = await _locked(session, request_id)
    deadline, reason = await _deadline(session, sr)
    if deadline is None or not is_overdue(deadline, now):
        return False
    await _expire(session, sr, shortage, reason, now)
    return True


async def expire_overdue(session: AsyncSession, now: datetime | None = None) -> int:
    """The timer job (§6): expire every overdue request, one transaction each, and return how
    many it expired. Idempotent and safe with several workers running at once."""
    now = now or datetime.now(UTC)
    done = 0
    for request_id in await overdue_ids(session, now):
        try:
            done += await expire_one(session, request_id, now)
            await session.commit()
        except Exception:
            await session.rollback()
            log.exception("could not expire source request %s", request_id)
    return done
