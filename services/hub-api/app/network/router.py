from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.auth.capabilities import Capability
from app.auth.deps import org_scoped, require
from app.auth.models import User
from app.catalog.models import SupplierOffer
from app.db import SessionDep
from app.network import service
from app.network.schemas import DemandOut
from app.orgs.models import OrgType
from app.pagination import Cursor, Limit, Page, paginate

router = APIRouter(prefix="/network", tags=["network"])

# api-and-events.md (S10): supplier users who keep the offers (`po.respond`, SUPPLIER orgs only).
Supplier = Annotated[User, Depends(require(Capability.PO_RESPOND, OrgType.SUPPLIER))]


@router.get("/demand")
async def network_demand(
    user: Supplier, session: SessionDep, limit: Limit = 50, cursor: Cursor = None
) -> Page[DemandOut]:
    """Open shortfall totals for each product the caller's supplier org offers (one row per
    offer, oldest offer first; 0 when nothing is open). Totals only: no hospital, facility,
    shortage or count of them."""
    offers, next_cursor = await paginate(
        session,
        org_scoped(select(SupplierOffer), user),
        SupplierOffer.created_at,
        SupplierOffer.id,
        limit,
        cursor,
    )
    totals = await service.open_shortfall(session, (o.product_id for o in offers), user.org_id)
    return Page[DemandOut](
        items=[
            DemandOut(product_id=o.product_id, open_shortfall_qty=totals.get(o.product_id, 0))
            for o in offers
        ],
        next_cursor=next_cursor,
    )
