from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.capabilities import Capability
from app.auth.deps import require
from app.auth.models import User
from app.db import SessionDep
from app.metrics import service
from app.metrics.schemas import NetworkMetricsOut
from app.orgs.models import OrgType

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/network")
async def network_metrics(
    _: Annotated[User, Depends(require(Capability.AUDIT_READ, OrgType.PLATFORM))],
    session: SessionDep,
) -> NetworkMetricsOut:
    """Network-wide figures across every org: platform users with `audit.read` only (403
    for anyone else, as the figures span other orgs)."""
    return await service.network(session)
