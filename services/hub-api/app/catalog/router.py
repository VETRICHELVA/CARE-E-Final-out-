import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.auth.capabilities import Capability
from app.auth.deps import CurrentUser, org_scoped, require
from app.auth.models import User
from app.catalog import service
from app.catalog.models import Product, SupplierOffer
from app.catalog.schemas import OfferIn, OfferOut, ProductOut
from app.db import SessionDep
from app.orgs.models import OrgType
from app.pagination import Cursor, Limit, Page, paginate

router = APIRouter(tags=["catalog"])


@router.get("/products")
async def list_products(
    _: CurrentUser, session: SessionDep, limit: Limit = 50, cursor: Cursor = None
) -> Page[ProductOut]:
    """The shared catalog; any signed-in user."""
    rows, next_cursor = await paginate(
        session, select(Product), Product.created_at, Product.id, limit, cursor
    )
    return Page[ProductOut](
        items=[ProductOut.model_validate(p) for p in rows], next_cursor=next_cursor
    )


@router.get("/products/{product_id}")
async def get_product(product_id: uuid.UUID, _: CurrentUser, session: SessionDep) -> ProductOut:
    return ProductOut.model_validate(await service.get_product(session, product_id))


@router.get("/supplier-offers")
async def list_offers(
    user: CurrentUser, session: SessionDep, limit: Limit = 50, cursor: Cursor = None
) -> Page[OfferOut]:
    """The caller's own org's offers only."""
    stmt = org_scoped(select(SupplierOffer), user)
    rows, next_cursor = await paginate(
        session, stmt, SupplierOffer.created_at, SupplierOffer.id, limit, cursor
    )
    return Page[OfferOut](items=[OfferOut.model_validate(o) for o in rows], next_cursor=next_cursor)


@router.put("/supplier-offers")
async def put_offer(
    body: OfferIn,
    user: Annotated[User, Depends(require(Capability.PO_RESPOND, OrgType.SUPPLIER))],
    session: SessionDep,
) -> OfferOut:
    """Create or update one offer of the caller's supplier org (SUPPLIER orgs only)."""
    offer = await service.put_offer(session, user, body)
    await session.commit()
    return OfferOut.model_validate(offer)
