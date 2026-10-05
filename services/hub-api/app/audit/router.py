import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.audit.models import AuditLog
from app.audit.schemas import AuditOut
from app.auth.capabilities import Capability
from app.auth.deps import is_platform_admin, org_scoped, require
from app.auth.models import User
from app.db import NulFreeStr, SessionDep
from app.pagination import Cursor, Limit, Page, paginate

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
async def list_audit(
    user: Annotated[User, Depends(require(Capability.AUDIT_READ))],
    session: SessionDep,
    entity: NulFreeStr | None = None,
    entity_id: uuid.UUID | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[AuditOut]:
    """Own org's rows, newest first; the platform admin sees every org's rows."""
    stmt = select(AuditLog)
    if not is_platform_admin(user):
        stmt = org_scoped(stmt, user)
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    rows, next_cursor = await paginate(
        session, stmt, AuditLog.ts, AuditLog.id, limit, cursor, newest_first=True
    )
    return Page[AuditOut](items=[AuditOut.model_validate(r) for r in rows], next_cursor=next_cursor)
