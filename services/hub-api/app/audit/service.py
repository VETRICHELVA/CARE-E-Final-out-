import uuid
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.auth.models import User

NO_REASON = "No reason was entered."


async def record(
    session: AsyncSession,
    actor: User | None,
    entity: str,
    entity_id: uuid.UUID,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    reason: str | None,
    *,
    org_id: uuid.UUID | None = None,
) -> AuditLog:
    """Write one append-only audit row in the caller's transaction (business-rules.md §10).

    User actor: a non-blank reason -> USER; blank -> SYSTEM, "No reason was entered."
    System actor (actor=None): must pass the factual cause as `reason` and an `org_id`.
    """
    reason = (reason or "").strip()
    if actor is None:
        if not reason or org_id is None:
            raise ValueError("System actors must pass an explicit cause and org_id.")
        source = "SYSTEM"
    elif reason:
        source = "USER"
    else:
        reason, source = NO_REASON, "SYSTEM"
    row = AuditLog(
        actor_id=actor.id if actor else None,
        org_id=org_id or (actor.org_id if actor else None),
        entity=entity,
        entity_id=entity_id,
        action=action,
        before=jsonable_encoder(before),
        after=jsonable_encoder(after),
        reason=reason,
        reason_source=source,
    )
    session.add(row)
    await session.flush()
    return row
