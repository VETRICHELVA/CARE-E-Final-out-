import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.capabilities import Capability
from app.auth.deps import CurrentUser, require
from app.auth.models import User
from app.db import SessionDep
from app.domain.recommendation import APPROVED_MESSAGE
from app.recommendations import service
from app.recommendations.schemas import ApprovalOut, RecommendationOut
from app.shortages.schemas import ReasonIn
from app.source_requests.service import Settled

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

# api-and-events.md (S09): deciding needs `recommendation.approve` in the requesting org.
Approver = Annotated[User, Depends(require(Capability.RECOMMENDATION_APPROVE))]


@router.get("/{recommendation_id}")
async def get_recommendation(
    recommendation_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> RecommendationOut:
    """Any user of the requester's org (403 otherwise). A hospital source's cost is never
    shown, only supplier costs."""
    return RecommendationOut.of(await service.get(session, user, recommendation_id))


@router.post("/{recommendation_id}/approve")
async def approve_recommendation(
    recommendation_id: uuid.UUID, user: Approver, session: SessionDep, body: ReasonIn | None = None
) -> ApprovalOut:
    """PENDING or ESCALATED -> APPROVED. Transfer: holds become FIRM, requests CONFIRMED,
    one shipment per source. Buy: a purchase order is sent. The shortage -> IN_FULFILLMENT.
    409 if already decided, or if it (or a hold) has expired: it is then expired and
    matching re-runs."""
    try:
        rec, shipment_ids, po_id = await service.approve(
            session, user, recommendation_id, body.reason if body else None
        )
    except Settled as e:
        await session.commit()  # keep the expiry and the re-run
        raise e.error from None
    out = ApprovalOut(
        recommendation=RecommendationOut.of(rec),
        message=APPROVED_MESSAGE[rec.type],
        shipment_ids=shipment_ids,
        purchase_order_id=po_id,
    )
    await session.commit()
    return out


@router.post("/{recommendation_id}/reject")
async def reject_recommendation(
    recommendation_id: uuid.UUID, user: Approver, session: SessionDep, body: ReasonIn | None = None
) -> RecommendationOut:
    """PENDING or ESCALATED -> REJECTED (reason optional). Releases every hold and re-runs
    matching. 409 if already decided or expired."""
    try:
        rec = await service.reject(session, user, recommendation_id, body.reason if body else None)
    except Settled as e:
        await session.commit()
        raise e.error from None
    out = RecommendationOut.of(rec)
    await session.commit()
    return out


@router.post("/{recommendation_id}/escalate")
async def escalate_recommendation(
    recommendation_id: uuid.UUID, user: Approver, session: SessionDep, body: ReasonIn | None = None
) -> RecommendationOut:
    """PENDING -> ESCALATED, notifying every APPROVER of the org. 409 otherwise."""
    try:
        rec = await service.escalate(
            session, user, recommendation_id, body.reason if body else None
        )
    except Settled as e:
        await session.commit()
        raise e.error from None
    out = RecommendationOut.of(rec)
    await session.commit()
    return out
