import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity


class Notification(Entity):
    """A message for one user, e.g. an escalated recommendation for every APPROVER of the
    org (business-rules.md §8). Users read their own with GET /notifications (S12)."""

    __tablename__ = "notification"
    # GET /notifications: a user's notifications, newest first.
    __table_args__ = (Index("ix_notification_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
