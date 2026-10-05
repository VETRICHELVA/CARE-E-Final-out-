import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.auth.deps import CurrentUser
from app.db import SessionDep
from app.errors import AppError
from app.orgs.models import Facility, Organization
from app.orgs.schemas import FacilityOut, OrgOut, PublicFacilityView, PublicOrgView
from app.pagination import Cursor, Limit, Page, paginate

router = APIRouter(prefix="/orgs", tags=["orgs"])


async def _get_org(session: SessionDep, org_id: uuid.UUID) -> Organization:
    org = await session.get(Organization, org_id)
    if org is None:
        raise AppError(404, "not_found", "Organization not found.")
    return org


@router.get("/{org_id}")
async def get_org(
    org_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> OrgOut | PublicOrgView:
    """Own org in full; any other org: name, type and location only."""
    org = await _get_org(session, org_id)
    return OrgOut.of(org) if org.id == user.org_id else PublicOrgView.of(org)


@router.get("/{org_id}/facilities")
async def list_facilities(
    org_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[FacilityOut] | Page[PublicFacilityView]:
    """Own org's facilities in full; another org's: name and location only."""
    await _get_org(session, org_id)
    stmt = select(Facility).where(Facility.org_id == org_id)
    rows, next_cursor = await paginate(
        session, stmt, Facility.created_at, Facility.id, limit, cursor
    )
    if org_id == user.org_id:
        return Page[FacilityOut](items=[FacilityOut.of(f) for f in rows], next_cursor=next_cursor)
    return Page[PublicFacilityView](
        items=[PublicFacilityView.of(f) for f in rows], next_cursor=next_cursor
    )
