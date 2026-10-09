"""Reliability scores and credits (business-rules.md §5, §12; S19).

Scores are stored, one row per org, and matching reads them (`scores`); nothing computes a
score inline. They are recomputed nightly (`recompute_all`, the worker) and, for the source
orgs of a shortage's shipments, when the shortage is reconciled (`on_reconciled`). Credits are
written at reconciliation only, from what the receiver accepted on a transfer's shipment.

Every figure comes from a recorded outcome (CLAUDE.md rule 5): source request answers and
their times (a supplier: its purchase-order answers, timed by the append-only audit row of
its first answer), shipments recorded DELIVERED, and Reconciliation rows.

Lock order: a recompute locks only the org's score row (in org id order when there are
several), after whatever its caller holds. The nightly job holds nothing else, so the two
never wait on each other in a cycle; a recompute that waits for another sees that one's
committed outcomes once it has the lock (READ COMMITTED), so the later write is complete."""

import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.audit.models import AuditLog
from app.domain import config
from app.domain import reliability as rules
from app.domain.fulfillment import PoStatus, ShipmentStatus
from app.domain.source_request import RequestStatus
from app.orgs.models import Organization, OrgType
from app.purchase_orders.models import PurchaseOrder
from app.receiving.models import Reconciliation
from app.shipments.models import Shipment
from app.shortages.models import Shortage
from app.source_requests.models import SourceRequest
from app.trust.models import CreditLedger, ReliabilityScore

log = logging.getLogger("app.trust")

CREDIT = "credit_ledger"
PURCHASE_ORDER = "purchase_order"  # app.purchase_orders.service.ENTITY
SOURCE_TYPES = (OrgType.HOSPITAL, OrgType.SUPPLIER)


# --- reads -----------------------------------------------------------------------------------


async def scores(session: AsyncSession, org_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
    """The stored score of each org; an org without a stored score has the no-history
    default (§5)."""
    ids = set(org_ids)
    stored: dict[uuid.UUID, int] = {}
    if ids:
        rows = await session.execute(
            select(ReliabilityScore.org_id, ReliabilityScore.score).where(
                ReliabilityScore.org_id.in_(ids)
            )
        )
        stored = {org_id: score for org_id, score in rows}
    return {org_id: stored.get(org_id, config.DEFAULT_RELIABILITY) for org_id in ids}


async def stored(session: AsyncSession, org_id: uuid.UUID) -> ReliabilityScore | None:
    return await session.scalar(select(ReliabilityScore).where(ReliabilityScore.org_id == org_id))


async def balance(session: AsyncSession, org_id: uuid.UUID) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(CreditLedger.delta), 0)).where(CreditLedger.org_id == org_id)
    )
    return int(total or 0)


# --- history -------------------------------------------------------------------------------


def _minutes(td_seconds: float) -> float:
    return td_seconds / 60


async def history(session: AsyncSession, org_id: uuid.UUID) -> rules.History:
    """The org's recorded outcomes as a source (see app.domain.reliability): a supplier's
    answers are its purchase orders', any other org's its source requests'."""
    org_type = await session.scalar(select(Organization.type).where(Organization.id == org_id))
    supplier = org_type == OrgType.SUPPLIER
    requests = [] if supplier else await _request_answers(session, org_id)
    orders = await _order_answers(session, org_id) if supplier else []
    return rules.History(
        answers=rules.answers_for(str(org_type), requests, orders),
        on_time=await _on_time(session, org_id),
        reconciled=await _reconciled(session, org_id),
    )


async def _request_answers(session: AsyncSession, org_id: uuid.UUID) -> list[rules.Answer]:
    requests = await session.execute(
        select(
            SourceRequest.status,
            SourceRequest.created_at,
            SourceRequest.responded_at,
            Shortage.priority,
        )
        .join(Shortage, Shortage.id == SourceRequest.shortage_id)
        .where(
            SourceRequest.source_org_id == org_id,
            # Answered, or left to expire. Still waiting, or superseded before an answer: not
            # counted (the source never had its full chance to answer).
            (SourceRequest.responded_at.is_not(None))
            | (SourceRequest.status == RequestStatus.EXPIRED),
        )
    )
    answers = []
    for status, created_at, responded_at, priority in requests:
        limit = config.SOURCE_RESPONSE_LIMIT[priority].total_seconds() / 60
        if responded_at is None:
            answers.append(rules.Answer(False, None, limit))
            continue
        minutes = _minutes((responded_at - created_at).total_seconds())
        answers.append(rules.Answer(status != RequestStatus.DECLINED, minutes, limit))
    return answers


async def _order_answers(session: AsyncSession, org_id: uuid.UUID) -> list[rules.OrderAnswer]:
    """The supplier's answered purchase orders. The answer's time is the audit row of its
    first move out of SENT (ACKNOWLEDGED or REJECTED), in the supplier's own org: the
    supplier's recorded action. An order still SENT has no such row and is not counted."""
    first_answer = func.min(AuditLog.ts)
    rows = await session.execute(
        select(PurchaseOrder.created_at, PurchaseOrder.status, Shortage.priority, first_answer)
        .join(Shortage, Shortage.id == PurchaseOrder.shortage_id)
        .join(
            AuditLog,
            (AuditLog.entity == PURCHASE_ORDER)
            & (AuditLog.entity_id == PurchaseOrder.id)
            & (AuditLog.org_id == org_id)
            & (AuditLog.action == f"{PURCHASE_ORDER}.status_changed")
            & (AuditLog.before["status"].astext == PoStatus.SENT),
        )
        .where(PurchaseOrder.supplier_org_id == org_id)
        .group_by(PurchaseOrder.id, Shortage.priority)
    )
    return [
        rules.OrderAnswer(
            sent_at=created_at,
            answered_at=answered_at,
            rejected=status == PoStatus.REJECTED,
            sla_minutes=config.SOURCE_RESPONSE_LIMIT[priority].total_seconds() / 60,
        )
        for created_at, status, priority, answered_at in rows
    ]


async def _on_time(session: AsyncSession, org_id: uuid.UUID) -> list[bool]:
    on_time = []
    shipments = await session.execute(
        select(Shipment.status_history, Shortage.required_by)
        .join(Shortage, Shortage.id == Shipment.shortage_id)
        .where(
            Shipment.from_org_id == org_id,
            Shipment.status.in_((ShipmentStatus.DELIVERED, ShipmentStatus.RECONCILED)),
        )
    )
    for status_history, required_by in shipments:
        delivered = [
            datetime.fromisoformat(h["at"])
            for h in status_history or []
            if h.get("to") == ShipmentStatus.DELIVERED and h.get("at")
        ]
        if delivered:  # only a recorded delivery time counts
            on_time.append(min(delivered) <= required_by)
    return on_time


async def _reconciled(session: AsyncSession, org_id: uuid.UUID) -> list[tuple[int, int]]:
    reconciled = await session.execute(
        select(Reconciliation.expected, Reconciliation.discrepancy)
        .join(Shipment, Shipment.id == Reconciliation.shipment_id)
        .where(Shipment.from_org_id == org_id)
    )
    return [(expected, discrepancy) for expected, discrepancy in reconciled]


# --- writes ----------------------------------------------------------------------------------


async def recompute(
    session: AsyncSession, org_id: uuid.UUID, now: datetime | None = None
) -> ReliabilityScore:
    """Recompute and store the org's score under its row lock. The caller commits."""
    now = now or datetime.now(UTC)
    await session.execute(
        insert(ReliabilityScore)
        .values(
            id=uuid.uuid4(),
            org_id=org_id,
            score=config.DEFAULT_RELIABILITY,
            computed_at=now,
        )
        .on_conflict_do_nothing(index_elements=[ReliabilityScore.org_id])
    )
    row = await session.scalar(
        select(ReliabilityScore)
        .where(ReliabilityScore.org_id == org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert row is not None
    c = rules.components(await history(session, org_id))
    row.acceptance_rate = c.acceptance_rate
    row.median_response_minutes = c.median_response_minutes
    row.response_speed = c.response_speed
    row.on_time_rate = c.on_time_rate
    row.discrepancy_rate = c.discrepancy_rate
    row.score = rules.score(c)
    row.computed_at = now
    await session.flush()
    return row


async def recompute_all(session: AsyncSession, now: datetime | None = None) -> int:
    """The nightly job: every hospital and supplier org, one transaction each. Returns how
    many it stored. Idempotent and safe with several workers (each row is locked)."""
    now = now or datetime.now(UTC)
    org_ids = list(
        await session.scalars(
            select(Organization.id)
            .where(Organization.type.in_(SOURCE_TYPES))
            .order_by(Organization.id)
        )
    )
    await session.commit()
    done = 0
    for org_id in org_ids:
        try:
            await recompute(session, org_id, now)
            await session.commit()
            done += 1
        except Exception:
            await session.rollback()
            log.exception("could not recompute reliability for org %s", org_id)
    return done


async def on_reconciled(session: AsyncSession, shortage: Shortage, now: datetime) -> None:
    """§12, at reconciliation: credit each transfer's source org for what the receiver
    accepted (never for units shipped but rejected), then recompute the score of every source
    org of the shortage's shipments. In the reconciliation's transaction."""
    rows = (
        await session.execute(
            select(Shipment.from_org_id, Shipment.source_request_id, Reconciliation.accepted)
            .join(Reconciliation, Reconciliation.shipment_id == Shipment.id)
            .where(Reconciliation.shortage_id == shortage.id)
            .order_by(Shipment.from_org_id)
        )
    ).all()
    accepted_by_org: dict[uuid.UUID, int] = {}
    for from_org_id, source_request_id, accepted in rows:
        if source_request_id is not None:  # a transfer (a purchase earns no credits)
            accepted_by_org[from_org_id] = accepted_by_org.get(from_org_id, 0) + accepted
    for org_id, accepted in accepted_by_org.items():
        await _credit(session, org_id, shortage, accepted)
    for org_id in sorted({from_org_id for from_org_id, _, _ in rows}):
        await recompute(session, org_id, now)


async def _credit(
    session: AsyncSession, org_id: uuid.UUID, shortage: Shortage, accepted: int
) -> CreditLedger | None:
    delta = rules.credits_for(accepted)
    if delta == 0:
        return None
    entry = CreditLedger(
        id=uuid.uuid4(),
        org_id=org_id,
        delta=delta,
        reason=rules.credit_reason(accepted),
        shortage_id=shortage.id,
    )
    session.add(entry)
    await session.flush()
    # A SYSTEM row in the credited org; no figure but the accepted units and the credits.
    await audit.record(
        session,
        None,
        CREDIT,
        entry.id,
        f"{CREDIT}.credited",
        None,
        {"delta": delta, "accepted": accepted, "shortage_id": shortage.id},
        entry.reason,
        org_id=org_id,
    )
    return entry
