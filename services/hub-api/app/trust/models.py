import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Entity


class ReliabilityScore(Entity):
    """An org's stored reliability (business-rules.md §12): one row per org, recomputed
    nightly and after each reconciliation of its shipments. Matching reads `score`, never
    computes it. A component is null while the org has no history for it, and `score` is
    then the no-history default (70)."""

    __tablename__ = "reliability_score"
    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 100", name="score_range"),
        CheckConstraint(
            "(acceptance_rate IS NULL OR acceptance_rate BETWEEN 0 AND 1)"
            " AND (on_time_rate IS NULL OR on_time_rate BETWEEN 0 AND 1)"
            " AND (discrepancy_rate IS NULL OR discrepancy_rate BETWEEN 0 AND 1)"
            " AND (response_speed IS NULL OR response_speed >= 0)",
            name="rates",
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), unique=True)
    acceptance_rate: Mapped[float | None]
    median_response_minutes: Mapped[float | None]
    response_speed: Mapped[float | None]
    on_time_rate: Mapped[float | None]
    discrepancy_rate: Mapped[float | None]
    score: Mapped[int]
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CreditLedger(Base):
    """Credits earned by a source org (business-rules.md §12): +1 per 10 units transferred and
    reconciled, written at reconciliation. Append-only (a database trigger rejects UPDATE,
    DELETE and TRUNCATE); one row per source org and shortage. Not spendable in the MVP."""

    __tablename__ = "credit_ledger"
    __table_args__ = (
        CheckConstraint("delta <> 0", name="delta_nonzero"),
        UniqueConstraint("org_id", "shortage_id", name="uq_credit_ledger_org_shortage"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    delta: Mapped[int]
    reason: Mapped[str]
    shortage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shortage.id"))
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
