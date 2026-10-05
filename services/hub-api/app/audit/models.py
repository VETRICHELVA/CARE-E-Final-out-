import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AuditLog(Base):
    """Append-only: a database trigger rejects UPDATE, DELETE and TRUNCATE.
    `ts` is the row's only timestamp, since the row never changes."""

    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint("reason_source IN ('USER','SYSTEM')", name="reason_source"),
        Index("ix_audit_log_lookup", "org_id", "entity", "entity_id", "ts"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"))
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[uuid.UUID]
    action: Mapped[str] = mapped_column(String(64))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str]
    reason_source: Mapped[str] = mapped_column(String(8))
    # clock_timestamp(), not now(): rows written in one transaction keep their order.
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
