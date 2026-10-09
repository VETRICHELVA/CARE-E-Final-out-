import uuid

from fastapi import APIRouter

from app.auth.deps import CurrentUser
from app.db import SessionDep
from app.domain import config
from app.errors import AppError
from app.orgs.models import Organization
from app.trust import service
from app.trust.schemas import ReliabilityOut

router = APIRouter(prefix="/orgs", tags=["trust"])


@router.get("/{org_id}/reliability")
async def get_reliability(
    org_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> ReliabilityOut:
    """Any signed-in user may read any org's stored score and its four components (they rank
    and explain candidates). The credit balance is shown to the org's own users only."""
    if await session.get(Organization, org_id) is None:
        raise AppError(404, "not_found", "Organization not found.")
    row = await service.stored(session, org_id)
    credits = await service.balance(session, org_id) if org_id == user.org_id else None
    if row is None:
        return ReliabilityOut(
            org_id=org_id,
            score=config.DEFAULT_RELIABILITY,
            has_history=False,
            acceptance_rate=None,
            response_speed=None,
            median_response_minutes=None,
            on_time_rate=None,
            discrepancy_rate=None,
            computed_at=None,
            credits=credits,
        )
    components = (row.acceptance_rate, row.response_speed, row.on_time_rate, row.discrepancy_rate)
    return ReliabilityOut(
        org_id=org_id,
        score=row.score,
        has_history=None not in components,
        acceptance_rate=row.acceptance_rate,
        response_speed=row.response_speed,
        median_response_minutes=row.median_response_minutes,
        on_time_rate=row.on_time_rate,
        discrepancy_rate=row.discrepancy_rate,
        computed_at=row.computed_at,
        credits=credits,
    )
