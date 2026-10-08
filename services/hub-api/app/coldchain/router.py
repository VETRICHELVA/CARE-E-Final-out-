import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.auth.deps import CurrentUser
from app.coldchain import service
from app.coldchain.schemas import ColdChainOut
from app.db import SessionDep

router = APIRouter(tags=["coldchain"])


@router.get("/shipments/{shipment_id}/coldchain")
async def get_coldchain(
    shipment_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[
        int, Query(ge=1, le=10_000, description="How many of the newest readings to return.")
    ] = service.READINGS_LIMIT,
) -> ColdChainOut:
    """The shipment's readings and cold-chain events (business-rules.md §11), with the
    product's band and the cold box's battery and last seen. Only the shipment's from, to
    and carrier orgs (403 otherwise); the carrier sees its own devices' data only."""
    return await service.view(session, user, shipment_id, limit)
