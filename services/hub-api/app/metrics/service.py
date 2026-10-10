"""GET /metrics/network (S20): network-wide figures from recorded rows only (CLAUDE.md rule
5). Definitions: api-and-events.md "Network metrics"; arithmetic: app/domain/metrics.py.

No hospital's unit cost is read: cost avoided is priced at supplier prices the match runs
recorded (`Candidate.unit_price_paise`, supplier candidates only)."""

import uuid
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.coldchain.models import ColdChainEvent
from app.domain import metrics as rules
from app.domain.coldchain import ColdChainEventType
from app.domain.fulfillment import PoStatus, ShipmentStatus
from app.domain.recommendation import RecStatus
from app.domain.resolution import BUY, TRANSFER, TRANSFER_SPLIT
from app.domain.source_request import RequestStatus
from app.domain.surplus import SurplusStatus
from app.iot.models import SensorReading
from app.metrics.schemas import (
    ColdChainComplianceOut,
    CostAvoidedOut,
    ExpirySavedOut,
    NetworkMetricsOut,
    ResolutionMixOut,
    TimeToSourceOut,
)
from app.purchase_orders import service as purchase_orders
from app.purchase_orders.models import PurchaseOrder
from app.receiving.models import Receipt
from app.recommendations.models import Recommendation
from app.shipments.models import Shipment
from app.shortages.models import Candidate, Shortage, SourceType
from app.source_requests.models import Hold, SourceRequest
from app.surplus.models import SurplusPost


async def network(session: AsyncSession, now: datetime | None = None) -> NetworkMetricsOut:
    cost, saved = await _transfers_received(session)
    return NetworkMetricsOut(
        computed_at=now or datetime.now(UTC),
        time_to_confirmed_source=await _time_to_source(session),
        resolution_mix=await _resolution_mix(session),
        procurement_cost_avoided=cost,
        units_saved_from_expiry=saved,
        cold_chain=await _cold_chain(session),
    )


async def _time_to_source(session: AsyncSession) -> TimeToSourceOut:
    """A source is confirmed when a hospital accepts its source request (`responded_at` of
    a request not DECLINED) or a supplier acknowledges its purchase order (the append-only
    audit row of SENT -> ACKNOWLEDGED). The shortage's first confirmation counts."""
    first: dict[uuid.UUID, datetime] = {}

    def keep(shortage_id: uuid.UUID, ts: datetime) -> None:
        if shortage_id not in first or ts < first[shortage_id]:
            first[shortage_id] = ts

    accepted = await session.execute(
        select(SourceRequest.shortage_id, func.min(SourceRequest.responded_at))
        .where(
            SourceRequest.responded_at.is_not(None),
            SourceRequest.status != RequestStatus.DECLINED,
        )
        .group_by(SourceRequest.shortage_id)
    )
    for shortage_id, ts in accepted:
        if ts is not None:
            keep(shortage_id, ts)
    acknowledged = await session.execute(
        select(PurchaseOrder.shortage_id, func.min(AuditLog.ts))
        .join(AuditLog, AuditLog.entity_id == PurchaseOrder.id)
        .where(
            AuditLog.entity == purchase_orders.ENTITY,
            AuditLog.after["status"].astext == PoStatus.ACKNOWLEDGED,
        )
        .group_by(PurchaseOrder.shortage_id)
    )
    for shortage_id, ts in acknowledged:
        keep(shortage_id, ts)
    reported = {
        s: created for s, created in await session.execute(select(Shortage.id, Shortage.created_at))
    }
    pairs = [(reported[s], ts) for s, ts in first.items() if s in reported]
    return TimeToSourceOut(
        median_minutes=rules.median_minutes(pairs),
        shortages_confirmed=len(pairs),
        shortages_reported=len(reported),
    )


async def _resolution_mix(session: AsyncSession) -> ResolutionMixOut:
    counts: dict[str, int] = {
        rec_type: n
        for rec_type, n in await session.execute(
            select(Recommendation.type, func.count())
            .where(Recommendation.status == RecStatus.APPROVED)
            .group_by(Recommendation.type)
        )
    }
    transfers = counts.get(TRANSFER, 0) + counts.get(TRANSFER_SPLIT, 0)
    purchases = counts.get(BUY, 0)
    total = transfers + purchases
    return ResolutionMixOut(
        transfers=transfers,
        purchases=purchases,
        transfer_share=rules.share(transfers, total),
        purchase_share=rules.share(purchases, total),
    )


async def _transfers_received(session: AsyncSession) -> tuple[CostAvoidedOut, ExpirySavedOut]:
    """Every receipt on a hospital-to-hospital shipment (one with a source request), with
    the units accepted."""
    received = (
        await session.execute(
            select(
                SourceRequest.id,
                SourceRequest.created_at,
                Candidate.match_run_id,
                Receipt.accepted,
            )
            .join(Shipment, Shipment.id == Receipt.shipment_id)
            .join(SourceRequest, SourceRequest.id == Shipment.source_request_id)
            .join(Candidate, Candidate.id == SourceRequest.candidate_id)
            .where(Receipt.accepted > 0)
        )
    ).all()
    if not received:
        return CostAvoidedOut(paise=0, units_priced=0, units_unpriced=0), ExpirySavedOut(units=0)

    runs = {run_id for _, _, run_id, _ in received}
    cheapest: dict[uuid.UUID, int | None] = {
        run_id: price
        for run_id, price in await session.execute(
            select(Candidate.match_run_id, func.min(Candidate.unit_price_paise))
            .where(
                Candidate.match_run_id.in_(runs),
                Candidate.source_type == SourceType.SUPPLIER,
                Candidate.unit_price_paise.is_not(None),
            )
            .group_by(Candidate.match_run_id)
        )
    }

    request_ids = [request_id for request_id, *_ in received]
    held: dict[uuid.UUID, list[tuple[uuid.UUID, int]]] = defaultdict(list)
    for request_id, batch_id, qty in await session.execute(
        select(Hold.source_request_id, Hold.batch_id, Hold.qty).where(
            Hold.source_request_id.in_(request_ids)
        )
    ):
        held[request_id].append((batch_id, qty))
    posts: dict[uuid.UUID, list[tuple[datetime, int]]] = defaultdict(list)
    batch_ids = {batch_id for lots in held.values() for batch_id, _ in lots}
    for batch_id, created_at, qty in await session.execute(
        select(SurplusPost.batch_id, SurplusPost.created_at, SurplusPost.qty).where(
            SurplusPost.batch_id.in_(batch_ids), SurplusPost.status != SurplusStatus.WITHDRAWN
        )
    ):
        posts[batch_id].append((created_at, qty))

    def posted_before(batch_id: uuid.UUID, ts: datetime) -> int | None:
        """The latest surplus post on the batch made before `ts` (its qty), if any."""
        earlier = [p for p in posts.get(batch_id, []) if p[0] <= ts]
        return max(earlier)[1] if earlier else None

    paise = priced = unpriced = saved = 0
    for request_id, requested_at, run_id, accepted in received:
        avoided = rules.cost_avoided_paise(accepted, cheapest.get(run_id))
        if avoided is None:
            unpriced += accepted
        else:
            paise += avoided
            priced += accepted
        lots = [
            rules.HeldLot(qty, posted_before(batch_id, requested_at))
            for batch_id, qty in held.get(request_id, [])
        ]
        saved += rules.units_saved_from_expiry(accepted, lots)
    return (
        CostAvoidedOut(paise=paise, units_priced=priced, units_unpriced=unpriced),
        ExpirySavedOut(units=saved),
    )


async def _cold_chain(session: AsyncSession) -> ColdChainComplianceOut:
    delivered = select(Shipment.id).where(
        Shipment.requires_cold_chain.is_(True),
        Shipment.status.in_([ShipmentStatus.DELIVERED, ShipmentStatus.RECONCILED]),
    )
    deliveries = await session.scalar(select(func.count()).select_from(delivered.subquery()))
    monitored = set(
        await session.scalars(
            select(SensorReading.shipment_id)
            .where(SensorReading.shipment_id.in_(delivered))
            .distinct()
        )
    )
    flagged: dict[str, set[uuid.UUID]] = defaultdict(set)
    for shipment_id, event_type in await session.execute(
        select(ColdChainEvent.shipment_id, ColdChainEvent.type)
        .where(
            ColdChainEvent.shipment_id.in_(monitored),
            ColdChainEvent.type.in_(
                [ColdChainEventType.EXCURSION, ColdChainEventType.DEVICE_SILENT]
            ),
        )
        .distinct()
    ):
        flagged[event_type].add(shipment_id)
    excursions = len(flagged[ColdChainEventType.EXCURSION])
    return ColdChainComplianceOut(
        deliveries=deliveries or 0,
        monitored=len(monitored),
        with_excursion=excursions,
        with_device_silent=len(flagged[ColdChainEventType.DEVICE_SILENT]),
        compliance_rate=rules.share(len(monitored) - excursions, len(monitored)),
    )
