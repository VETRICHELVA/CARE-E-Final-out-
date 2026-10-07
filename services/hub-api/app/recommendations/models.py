import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity
from app.domain.recommendation import RecStatus
from app.domain.resolution import BUY, TRANSFER, TRANSFER_SPLIT

TYPES = (TRANSFER, TRANSFER_SPLIT, BUY)


def _one_of(column: str, values: tuple[str, ...] | type[RecStatus]) -> str:
    return f"{column} IN ({','.join(repr(str(v)) for v in values)})"


class Recommendation(Entity):
    """The hub's recommendation for one match run (business-rules.md §7 step 4).

    `lines` and `alternatives` are stored in full, hospital landed cost included (like the
    match run's plan); responses to the requester drop what would reveal a hospital's unit
    cost (CLAUDE.md rule 6). `reason` is what the decider typed (null if nothing was typed)
    or a timer's factual cause; `reason_source` says which."""

    __tablename__ = "recommendation"
    __table_args__ = (
        CheckConstraint(_one_of("status", RecStatus), name="status"),
        CheckConstraint(_one_of("type", TYPES), name="type"),
        CheckConstraint(
            "reason_source IS NULL OR reason_source IN ('USER','SYSTEM')", name="reason_source"
        ),
        # A shortage waits on at most one recommendation at a time.
        Index(
            "uq_recommendation_open",
            "shortage_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING','ESCALATED')"),
        ),
        # The timer job's scan for recommendations past their validity.
        Index(
            "ix_recommendation_open_expiry",
            "expires_at",
            postgresql_where=text("status IN ('PENDING','ESCALATED')"),
        ),
    )

    shortage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shortage.id"), index=True)
    # A match run has at most one recommendation.
    match_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("match_run.id"), unique=True)
    type: Mapped[str] = mapped_column(String(16))
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    alternatives: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    explanation: Mapped[str]
    status: Mapped[str] = mapped_column(String(12))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None]
    reason_source: Mapped[str | None] = mapped_column(String(8))
