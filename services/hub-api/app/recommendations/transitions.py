"""Low-level recommendation changes. Like `app.source_requests.holds`, it imports no other
service, so `app.shortages.service` can close a shortage's open recommendation without an
import cycle.

Audit (business-rules.md §10, one row per state change): recommendation rows go to the
requester's org, whose shortage the trail follows."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.models import User
from app.domain.events import EventType
from app.domain.recommendation import (
    OPEN_RECOMMENDATION,
    REC_TRANSITIONS,
    SHORTAGE_CANCELLED,
    RecStatus,
)
from app.domain.shortage import Status
from app.domain.state_machine import transition
from app.events import service as events
from app.recommendations.models import Recommendation
from app.shortages.models import Shortage

ENTITY = "recommendation"
BACK_TO_MATCHING = "The shortage went back to matching."


async def open_for(
    session: AsyncSession, shortage_id: uuid.UUID, *, lock: bool = False
) -> Recommendation | None:
    """The shortage's recommendation still waiting for a decision (at most one)."""
    stmt = select(Recommendation).where(
        Recommendation.shortage_id == shortage_id,
        Recommendation.status.in_(OPEN_RECOMMENDATION),
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(stmt)


async def move(
    session: AsyncSession,
    shortage: Shortage,
    rec: Recommendation,
    to: RecStatus,
    actor: User | None,
    why: str | None,
    **changes: Any,
) -> None:
    """Every recommendation transition: state machine (409 if not allowed), extra fields
    (such as the stored `reason`), one audit row with `why` as its reason, and
    `recommendation.status_changed` to the requester's org."""
    before = transition(rec, to, REC_TRANSITIONS)
    for name, value in changes.items():
        setattr(rec, name, value)
    await session.flush()
    await session.refresh(rec)
    await audit.record(
        session,
        actor,
        ENTITY,
        rec.id,
        f"{ENTITY}.status_changed",
        {"status": before},
        {"status": rec.status, **changes},
        why,
        org_id=shortage.org_id,
    )
    await events.emit(
        session,
        EventType.RECOMMENDATION_STATUS_CHANGED,
        [shortage.org_id],
        {"recommendation_id": rec.id, "shortage_id": shortage.id, "from": before, "to": rec.status},
    )


async def close_open(
    session: AsyncSession, shortage: Shortage, to: Status, reason: str | None, now: datetime
) -> None:
    """The shortage is leaving AWAITING_DECISION other than by approval: a hold expired
    (back to MATCHING) or the shortage was cancelled. Its open recommendation can no longer
    be approved, so it becomes EXPIRED with the factual SYSTEM cause. Rejection and expiry
    end the recommendation first, so they find nothing here."""
    rec = await open_for(session, shortage.id, lock=True)
    if rec is None:
        return
    if to == Status.CANCELLED:
        cause = SHORTAGE_CANCELLED
    else:
        cause = (reason or "").strip() or BACK_TO_MATCHING
    await move(
        session,
        shortage,
        rec,
        RecStatus.EXPIRED,
        None,
        cause,
        decided_at=now,
        reason=cause,
        reason_source="SYSTEM",
    )
