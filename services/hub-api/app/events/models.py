import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Entity
from app.domain.webhooks import DeliveryStatus

# Publish order. The publisher takes values one row at a time, under one advisory lock, so
# `seq` is the order clients received events in: the SSE `id` and the Last-Event-ID cursor.
PUBLISH_SEQ = Sequence("event_outbox_publish_seq", metadata=Base.metadata)


class EventOutbox(Entity):
    """An event written in the same transaction as its state change; a rolled-back change
    leaves no row, so it is never published. `payload` is the full envelope
    `{id, type, occurred_at, org_ids, data}`; `org_ids` is repeated as a column for the
    stream's replay query. `seq` and `published_at` are set when the worker publishes it."""

    __tablename__ = "event_outbox"
    __table_args__ = (
        CheckConstraint("cardinality(org_ids) > 0", name="has_orgs"),
        Index(
            "ix_event_outbox_unpublished",
            "created_at",
            "id",
            postgresql_where=text("published_at IS NULL"),
        ),
        # The pruning job's scan by age (S20).
        Index(
            "ix_event_outbox_published_at",
            "published_at",
            postgresql_where=text("published_at IS NOT NULL"),
        ),
    )

    event_type: Mapped[str] = mapped_column(String(64))
    org_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seq: Mapped[int | None] = mapped_column(BigInteger, unique=True)


class EventPruneMark(Base):
    """One row (S20): the highest `seq` the pruning job has deleted. A client resuming from an
    older Last-Event-ID may have missed pruned events, so it gets `reset` instead of a replay
    with holes (gaps in `seq` alone prove nothing: a rolled-back publish leaves one)."""

    __tablename__ = "event_prune_mark"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False, default=1)
    seq: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class WebhookSubscription(Entity):
    """An org's webhook: every event of `event_types` addressed to `org_id` is POSTed to `url`,
    signed with `secret` (shown once, when the subscription is created)."""

    __tablename__ = "webhook_subscription"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    secret: Mapped[str] = mapped_column(String(128))
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String(64)))


class WebhookDelivery(Entity):
    """One row per attempt to deliver one event to one subscription (see DeliveryStatus).
    Deleting the subscription deletes its delivery rows."""

    __tablename__ = "webhook_delivery"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({','.join(repr(str(s)) for s in DeliveryStatus)})", name="status"
        ),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        UniqueConstraint("subscription_id", "event_id", "attempt"),
        Index(
            "ix_webhook_delivery_due",
            "next_attempt_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhook_subscription.id", ondelete="CASCADE")
    )
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_outbox.id"), index=True)
    attempt: Mapped[int]
    status: Mapped[str] = mapped_column(String(16))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_code: Mapped[int | None]
