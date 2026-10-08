import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.capabilities import Capability
from app.auth.deps import CurrentUser, require
from app.auth.models import User
from app.db import SessionDep
from app.domain.source_request import RequestStatus
from app.orgs.models import Organization
from app.pagination import Cursor, Limit, Page, paginate
from app.shortages.models import Shortage
from app.shortages.schemas import ReasonIn
from app.source_requests import service
from app.source_requests.models import Hold, SourceRequest
from app.source_requests.schemas import Direction, SourceRequestOut

router = APIRouter(prefix="/source-requests", tags=["source-requests"])

# api-and-events.md (S06): answering a request needs `source_request.respond`.
Responder = Annotated[User, Depends(require(Capability.SOURCE_REQUEST_RESPOND))]


async def _out(
    session: AsyncSession, user: User, rows: Sequence[SourceRequest]
) -> list[SourceRequestOut]:
    if not rows:
        return []
    shortage_ids = {sr.shortage_id for sr in rows}
    shortages = {
        s.id: s
        for s in await session.scalars(select(Shortage).where(Shortage.id.in_(shortage_ids)))
    }
    org_ids = {sr.source_org_id for sr in rows} | {s.org_id for s in shortages.values()}
    stmt_names = select(Organization.id, Organization.name).where(Organization.id.in_(org_ids))
    names = {org_id: name for org_id, name in await session.execute(stmt_names)}
    holds: dict[uuid.UUID, list[Hold]] = defaultdict(list)
    stmt = select(Hold).where(Hold.source_request_id.in_([sr.id for sr in rows]))
    for h in await session.scalars(stmt.order_by(Hold.created_at, Hold.id)):
        holds[h.source_request_id].append(h)
    return [
        SourceRequestOut.of(sr, shortages[sr.shortage_id], names, holds[sr.id], user.org_id)
        for sr in rows
    ]


@router.get("")
async def list_source_requests(
    direction: Direction,
    user: CurrentUser,
    session: SessionDep,
    status: RequestStatus | None = None,
    shortage_id: uuid.UUID | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[SourceRequestOut]:
    """`incoming`: requests the caller's org is asked to supply. `outgoing`: requests sent
    for the caller's org's shortages. Newest first; optional `status` and `shortage_id`."""
    stmt = select(SourceRequest)
    if direction == Direction.INCOMING:
        stmt = stmt.where(SourceRequest.source_org_id == user.org_id)
    else:
        own = select(Shortage.id).where(Shortage.org_id == user.org_id)
        stmt = stmt.where(SourceRequest.shortage_id.in_(own))
    if status is not None:
        stmt = stmt.where(SourceRequest.status == status)
    if shortage_id is not None:
        stmt = stmt.where(SourceRequest.shortage_id == shortage_id)
    rows, next_cursor = await paginate(
        session,
        stmt,
        SourceRequest.created_at,
        SourceRequest.id,
        limit,
        cursor,
        newest_first=True,
    )
    return Page[SourceRequestOut](items=await _out(session, user, rows), next_cursor=next_cursor)


@router.post("/{request_id}/accept")
async def accept_source_request(
    request_id: uuid.UUID, user: Responder, session: SessionDep, body: ReasonIn | None = None
) -> SourceRequestOut:
    """Source org only. Places TENTATIVE holds on the source's batches (earliest expiry
    first) and moves the request to TENTATIVE_HOLD. 409 `conflict` if the stock no longer
    covers it: the request is then EXPIRED ("Stock changed before acceptance.") and matching
    re-runs. 409 `invalid_transition` if it is not REQUESTED, or its deadline has passed."""
    try:
        sr = await service.accept(session, user, request_id, body.reason if body else None)
    except service.Settled as e:
        await session.commit()  # keep the expiry and the re-run
        raise e.error from None
    (out,) = await _out(session, user, [sr])
    await session.commit()
    return out


@router.post("/{request_id}/decline")
async def decline_source_request(
    request_id: uuid.UUID, user: Responder, session: SessionDep, body: ReasonIn | None = None
) -> SourceRequestOut:
    """Source org only; the reason is optional. Matching re-runs without this org for the
    shortage. 409 `invalid_transition` if it is not REQUESTED, or its deadline has passed."""
    try:
        sr = await service.decline(session, user, request_id, body.reason if body else None)
    except service.Settled as e:
        await session.commit()
        raise e.error from None
    (out,) = await _out(session, user, [sr])
    await session.commit()
    return out
