"""Notifications for users. Written here; an endpoint to read them arrives with the screens
that show them (S12)."""

import uuid
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.capabilities import RoleName
from app.auth.models import Role, User, UserRole
from app.notifications.models import Notification


async def notify_role(
    session: AsyncSession,
    org_id: uuid.UUID,
    role: RoleName,
    type_: str,
    payload: dict[str, Any],
) -> list[Notification]:
    """One Notification per active user of `org_id` who holds `role`."""
    user_ids = await session.scalars(
        select(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(User.org_id == org_id, User.is_active.is_(True), Role.name == role)
        .order_by(User.id)
    )
    body = jsonable_encoder(payload)
    rows = [Notification(user_id=uid, type=type_, payload=body) for uid in user_ids]
    session.add_all(rows)
    await session.flush()
    return rows
