import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.auth.deps import CurrentUser
from app.db import SessionDep
from app.notifications import service
from app.notifications.models import Notification
from app.notifications.schemas import NotificationOut
from app.pagination import Cursor, Limit, Page, paginate

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    user: CurrentUser,
    session: SessionDep,
    unread: Annotated[bool, Query(description="Only the ones not yet marked read.")] = False,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[NotificationOut]:
    """The caller's own notifications, newest first. Nobody reads another user's."""
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows, next_cursor = await paginate(
        session, stmt, Notification.created_at, Notification.id, limit, cursor, newest_first=True
    )
    return Page[NotificationOut](
        items=[NotificationOut.model_validate(n) for n in rows], next_cursor=next_cursor
    )


@router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> NotificationOut:
    """Marks one of the caller's notifications read; one already read keeps its first
    `read_at`. 403 for another user's notification."""
    notification = await service.mark_read(session, user, notification_id)
    out = NotificationOut.model_validate(notification)
    await session.commit()
    return out
