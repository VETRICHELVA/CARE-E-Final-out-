import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.auth.capabilities import Capability
from app.auth.deps import CurrentUser, org_scoped, require, require_any
from app.auth.models import User
from app.db import SessionDep
from app.errors import AppError
from app.pagination import Cursor, Limit, Page, paginate
from app.shortages import service
from app.shortages.models import Shortage
from app.shortages.schemas import MatchRunOut, ReasonIn, ShortageCreate, ShortageOut

router = APIRouter(prefix="/shortages", tags=["shortages"])

# api-and-events.md (S05): create, cancel and re-run need `shortage.create`; list and read
# also admit approvers, who decide on their org's recommendations (S09/S12).
Requester = Annotated[User, Depends(require(Capability.SHORTAGE_CREATE))]
Reader = Annotated[
    User,
    Depends(require_any(Capability.SHORTAGE_CREATE, Capability.RECOMMENDATION_APPROVE)),
]


@router.post("", status_code=201)
async def create_shortage(
    body: ShortageCreate, user: Requester, session: SessionDep
) -> ShortageOut:
    """Create a shortage in the caller's org. The hub computes the shortfall and runs the
    first match, so the shortage comes back MATCHING."""
    shortage = await service.create_shortage(session, user, body)
    await session.commit()
    return ShortageOut.model_validate(shortage)


@router.get("")
async def list_shortages(
    user: Reader, session: SessionDep, limit: Limit = 50, cursor: Cursor = None
) -> Page[ShortageOut]:
    """The caller's own org's shortages, newest first."""
    stmt = org_scoped(select(Shortage), user)
    rows, next_cursor = await paginate(
        session, stmt, Shortage.created_at, Shortage.id, limit, cursor, newest_first=True
    )
    return Page[ShortageOut](
        items=[ShortageOut.model_validate(s) for s in rows], next_cursor=next_cursor
    )


@router.get("/{shortage_id}")
async def get_shortage(shortage_id: uuid.UUID, user: Reader, session: SessionDep) -> ShortageOut:
    return ShortageOut.model_validate(await service.get_shortage(session, user, shortage_id))


@router.post("/{shortage_id}/cancel")
async def cancel_shortage(
    shortage_id: uuid.UUID, user: Requester, session: SessionDep, body: ReasonIn | None = None
) -> ShortageOut:
    """Allowed from OPEN, MATCHING or AWAITING_DECISION; otherwise 409 `invalid_transition`."""
    reason = body.reason if body else None
    shortage = await service.cancel_shortage(session, user, shortage_id, reason)
    await session.commit()
    return ShortageOut.model_validate(shortage)


@router.post("/{shortage_id}/match")
async def rerun_match(
    shortage_id: uuid.UUID, user: Requester, session: SessionDep, body: ReasonIn | None = None
) -> MatchRunOut:
    """Manual re-run while the shortage is OPEN or MATCHING; otherwise 409."""
    run = await service.rerun_match(session, user, shortage_id, body.reason if body else None)
    out = await service.match_run_out(session, run)
    await session.commit()
    return out


@router.get("/{shortage_id}/match-runs/latest")
async def latest_match_run(
    shortage_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> MatchRunOut:
    """Any user of the requester's org: eligible candidates by rank, then rejected ones with
    every failed gate's reason."""
    await service.get_shortage(session, user, shortage_id)
    run = await service.latest_run(session, shortage_id)
    if run is None:
        raise AppError(404, "not_found", "This shortage has no match run yet.")
    return await service.match_run_out(session, run)
