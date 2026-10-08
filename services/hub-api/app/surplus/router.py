import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.capabilities import Capability
from app.auth.deps import require
from app.auth.models import User
from app.db import SessionDep
from app.domain.surplus import LIVE, MatchKind, SurplusStatus, expiry_band
from app.forecasting.service import today
from app.inventory.models import InventoryBatch
from app.orgs.models import Facility, Organization, OrgType
from app.pagination import Cursor, Limit, Page, paginate
from app.shortages.schemas import ReasonIn
from app.surplus import service
from app.surplus.models import SurplusMatch, SurplusPost
from app.surplus.schemas import (
    Location,
    MatchReason,
    SurplusCreate,
    SurplusOfferOut,
    SurplusOut,
)

router = APIRouter(prefix="/surplus", tags=["surplus"])

# api-and-events.md (S18): `inventory.edit`, in a HOSPITAL org (only hospitals hold batches).
Editor = Annotated[User, Depends(require(Capability.INVENTORY_EDIT, OrgType.HOSPITAL))]


async def _own_out(session: AsyncSession, posts: list[SurplusPost]) -> list[SurplusOut]:
    day = today()
    offered = await service.offered(session, posts, day)
    matched = await service.matched_org_ids(session, (p.id for p in posts))
    return [
        SurplusOut(
            id=p.id,
            org_id=p.org_id,
            batch_id=p.batch_id,
            product_id=p.product_id,
            qty=p.qty,
            offered_qty=offered[p.id],
            expiry_date=p.expiry_date,
            min_price_paise=p.min_price_paise,
            status=SurplusStatus(p.status),
            matched_org_ids=matched[p.id],
            created_by=p.created_by,
            created_at=p.created_at,
        )
        for p in posts
    ]


async def _offer_out(
    session: AsyncSession, user: User, posts: list[SurplusPost]
) -> list[SurplusOfferOut]:
    """Other orgs' posts, as rule 6 allows: transferable qty, expiry band and location."""
    if not posts:
        return []
    day = today()
    offered = await service.offered(session, posts, day)
    matches = {
        m.surplus_id: m
        for m in await session.scalars(
            select(SurplusMatch).where(
                SurplusMatch.org_id == user.org_id,
                SurplusMatch.surplus_id.in_([p.id for p in posts]),
            )
        )
    }
    places = {
        batch_id: (org_name, facility)
        for batch_id, org_name, facility in await session.execute(
            select(InventoryBatch.id, Organization.name, Facility)
            .join(Facility, Facility.id == InventoryBatch.facility_id)
            .join(Organization, Organization.id == InventoryBatch.org_id)
            .where(InventoryBatch.id.in_([p.batch_id for p in posts]))
        )
    }
    out = []
    for p in posts:
        m, (org_name, facility) = matches[p.id], places[p.batch_id]
        out.append(
            SurplusOfferOut(
                id=p.id,
                org_id=p.org_id,
                org_name=org_name,
                product_id=p.product_id,
                offered_qty=offered[p.id],
                expiry_band=expiry_band(p.expiry_date, day),
                location=Location(facility_name=facility.name, lat=facility.lat, lng=facility.lng),
                status=SurplusStatus(p.status),
                match=MatchReason(
                    kind=MatchKind(m.kind),
                    shortage_id=m.shortage_id,
                    stockout_date=m.stockout_date,
                    matched_at=m.created_at,
                ),
            )
        )
    return out


@router.get("")
async def list_surplus(
    user: Editor,
    session: SessionDep,
    status: SurplusStatus | None = None,
    product_id: uuid.UUID | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[SurplusOut]:
    """The caller's org's own posts, newest first."""
    stmt = select(SurplusPost).where(SurplusPost.org_id == user.org_id)
    if status is not None:
        stmt = stmt.where(SurplusPost.status == status)
    if product_id is not None:
        stmt = stmt.where(SurplusPost.product_id == product_id)
    posts, next_cursor = await paginate(
        session, stmt, SurplusPost.created_at, SurplusPost.id, limit, cursor, newest_first=True
    )
    return Page[SurplusOut](items=await _own_out(session, posts), next_cursor=next_cursor)


@router.get("/incoming")
async def list_incoming_surplus(
    user: Editor,
    session: SessionDep,
    product_id: uuid.UUID | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[SurplusOfferOut]:
    """Other orgs' live (OPEN or MATCHED) posts matched to the caller's org, newest post
    first: offered qty, expiry band and location only (CLAUDE.md rule 6). A withdrawn or
    expired post leaves this list."""
    stmt = (
        select(SurplusPost)
        .join(SurplusMatch, SurplusMatch.surplus_id == SurplusPost.id)
        .where(SurplusMatch.org_id == user.org_id, SurplusPost.status.in_(LIVE))
    )
    if product_id is not None:
        stmt = stmt.where(SurplusPost.product_id == product_id)
    posts, next_cursor = await paginate(
        session, stmt, SurplusPost.created_at, SurplusPost.id, limit, cursor, newest_first=True
    )
    items = await _offer_out(session, user, posts)
    return Page[SurplusOfferOut](items=items, next_cursor=next_cursor)


@router.post("", status_code=201)
async def create_surplus(body: SurplusCreate, user: Editor, session: SessionDep) -> SurplusOut:
    """Offer one of the caller's org's batches to the network (from an expiry-risk
    suggestion or by hand), then match it. 400 if the batch has expired or `qty` exceeds its
    transferable (`details.transferable_qty`); 409 `conflict` if the batch already has a live
    post; 403 for another org's batch."""
    post = await service.create(session, user, body, today())
    (out,) = await _own_out(session, [post])
    await session.commit()
    return out


@router.post("/{surplus_id}/withdraw")
async def withdraw_surplus(
    surplus_id: uuid.UUID, user: Editor, session: SessionDep, body: ReasonIn | None = None
) -> SurplusOut:
    """OPEN or MATCHED → WITHDRAWN (posting org only, else 403); otherwise 409
    `invalid_transition`. A withdrawn post is never matched again."""
    post = await service.withdraw(session, user, surplus_id, body.reason if body else None)
    (out,) = await _own_out(session, [post])
    await session.commit()
    return out
