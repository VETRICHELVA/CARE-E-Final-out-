"""Reads behind the copilot's tools (S13). Every read goes through the same checks as the
user's own endpoint, with the user the AI acts for: their org (403 for another org's
record), their capabilities, and the same output schemas (so the same redaction)."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas import (
    AiColdChainOut,
    AiRecommendationRef,
    AiShipmentRef,
    AiShortageOut,
    AiShortageRef,
)
from app.auth.models import User
from app.catalog.models import Product
from app.domain.fulfillment import ShipmentStatus
from app.domain.shortage import Status
from app.errors import AppError
from app.iot.models import Device, SensorReading
from app.orgs.models import Facility, Organization
from app.recommendations.models import Recommendation
from app.shipments import service as shipments
from app.shipments.models import Shipment
from app.shortages import service as shortages
from app.shortages.models import Candidate, MatchRun, Shortage
from app.shortages.schemas import CandidateOut, ShortageOut
from app.source_requests import router as source_requests
from app.source_requests.models import SourceRequest


async def shortage_view(session: AsyncSession, user: User, shortage_id: uuid.UUID) -> AiShortageOut:
    shortage = await shortages.get_shortage(session, user, shortage_id)
    product = await session.get_one(Product, shortage.product_id)
    facility = await session.get_one(Facility, shortage.facility_id)
    requests = await session.scalars(
        select(SourceRequest)
        .where(SourceRequest.shortage_id == shortage.id)
        .order_by(SourceRequest.created_at.desc(), SourceRequest.id.desc())
    )
    recs = await session.scalars(
        select(Recommendation)
        .where(Recommendation.shortage_id == shortage.id)
        .order_by(Recommendation.created_at.desc(), Recommendation.id.desc())
    )
    sent = await session.execute(
        select(Shipment, Organization.name)
        .join(Organization, Organization.id == Shipment.from_org_id)
        .where(Shipment.shortage_id == shortage.id)
        .order_by(Shipment.created_at.desc(), Shipment.id.desc())
    )
    residuals = await session.scalars(
        select(Shortage)
        .where(Shortage.parent_shortage_id == shortage.id, Shortage.org_id == user.org_id)
        .order_by(Shortage.created_at, Shortage.id)
    )
    return AiShortageOut(
        **ShortageOut.model_validate(shortage).model_dump(),
        product_code=product.code,
        product_name=product.name,
        facility_name=facility.name,
        # The requester's view of each request (no source batches or responder).
        source_requests=await source_requests._out(session, user, list(requests)),
        recommendations=[
            AiRecommendationRef(id=r.id, type=r.type, status=r.status, created_at=r.created_at)
            for r in recs
        ],
        shipments=[
            AiShipmentRef(id=s.id, status=ShipmentStatus(s.status), qty=s.qty, from_org_name=name)
            for s, name in sent
        ],
        residual_shortages=[
            AiShortageRef(
                id=r.id,
                status=Status(r.status),
                qty_required=r.qty_required,
                shortfall=r.shortfall,
                created_at=r.created_at,
            )
            for r in residuals
        ],
    )


async def candidate_view(
    session: AsyncSession, user: User, candidate_id: uuid.UUID
) -> CandidateOut:
    """One candidate of a match run, as GET /shortages/{id}/match-runs/latest shows it."""
    row = (
        await session.execute(
            select(Candidate, MatchRun.shortage_id, Organization.name)
            .join(MatchRun, MatchRun.id == Candidate.match_run_id)
            .join(Organization, Organization.id == Candidate.source_org_id)
            .where(Candidate.id == candidate_id)
        )
    ).one_or_none()
    if row is None:
        raise AppError(404, "not_found", "Candidate not found.")
    candidate, shortage_id, org_name = row
    await shortages.get_shortage(session, user, shortage_id)  # 403 for another org's
    return CandidateOut.of(candidate, org_name)


async def coldchain_view(
    session: AsyncSession, user: User, shipment_id: uuid.UUID
) -> AiColdChainOut:
    shipment = await shipments.get_visible(session, user, shipment_id)
    count, first, last, low, high = (
        await session.execute(
            select(
                func.count(SensorReading.id),
                func.min(SensorReading.ts),
                func.max(SensorReading.ts),
                func.min(SensorReading.temp_c),
                func.max(SensorReading.temp_c),
            ).where(SensorReading.shipment_id == shipment.id)
        )
    ).one()
    return AiColdChainOut(
        shipment_id=shipment.id,
        requires_cold_chain=shipment.requires_cold_chain,
        device_id=await session.scalar(
            select(Device.device_id).where(Device.id == shipment.device_id)
        )
        if shipment.device_id
        else None,
        reading_count=count,
        first_reading_at=first,
        last_reading_at=last,
        min_temp_c=low,
        max_temp_c=high,
        has_open_excursion=await shipments.has_open_excursion(session, shipment),
        events=[],
    )
