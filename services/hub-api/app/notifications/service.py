"""Notifications for users. Written here; each user reads and marks read only their own
(GET /notifications, POST /notifications/{id}/read, S12). Marking one read is not a state
change of any business-rules §8 machine, so it writes no audit row."""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.capabilities import RoleName
from app.auth.models import Role, User, UserRole
from app.errors import AppError
from app.notifications.models import Notification


async def mark_read(session: AsyncSession, user: User, notification_id: uuid.UUID) -> Notification:
    """The caller's own notification only (403 otherwise); already read stays as it was."""
    notification = await session.get(Notification, notification_id, with_for_update=True)
    if notification is None:
        raise AppError(404, "not_found", "Notification not found.")
    if notification.user_id != user.id:
        raise AppError(403, "forbidden", "This notification is for another user.")
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        await session.flush()
        await session.refresh(notification)
    return notification


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
