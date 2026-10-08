import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity
from app.domain.reconciliation import Condition, Outcome


def _one_of(column: str, values: type[Condition | Outcome]) -> str:
    return f"{column} IN ({','.join(repr(str(v)) for v in values)})"


class Receipt(Entity):
    """What the receiving org recorded when a shipment arrived (business-rules.md §9): one
    per shipment. `expected` is the shipment's qty, never taken from the client. `batch_id`
    is the batch the accepted stock became in the receiver's inventory (null if none was
    accepted)."""

    __tablename__ = "receipt"
    __table_args__ = (
        CheckConstraint(_one_of("condition", Condition), name="condition"),
        CheckConstraint(
            "expected > 0 AND received >= 0 AND accepted >= 0 AND rejected >= 0",
            name="non_negative",
        ),
        CheckConstraint("received <= expected", name="received_le_expected"),
        CheckConstraint("accepted + rejected = received", name="accepted_rejected"),
        CheckConstraint("(accepted = 0) = (batch_id IS NULL)", name="batch"),
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipment.id"), unique=True)
    shortage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shortage.id"), index=True)
    expected: Mapped[int]
    received: Mapped[int]
    accepted: Mapped[int]
    rejected: Mapped[int]
    condition: Mapped[str] = mapped_column(String(20))
    inspection_note: Mapped[str | None]
    received_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_batch.id"))


class Reconciliation(Entity):
    """One per shipment, written when every shipment of the shortage has a receipt (§9).
    `discrepancy` = expected − accepted for this shipment; `outcome` and
    `residual_shortage_id` are the shortage's: PARTIAL exactly when a residual was opened."""

    __tablename__ = "reconciliation"
    __table_args__ = (
        CheckConstraint(_one_of("outcome", Outcome), name="outcome"),
        CheckConstraint("discrepancy = expected - accepted", name="discrepancy"),
        CheckConstraint(
            "(outcome = 'PARTIAL') = (residual_shortage_id IS NOT NULL)", name="residual"
        ),
    )

    shortage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shortage.id"), index=True)
    shipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipment.id"), unique=True)
    expected: Mapped[int]
    accepted: Mapped[int]
    discrepancy: Mapped[int]
    outcome: Mapped[str] = mapped_column(String(10))
    residual_shortage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shortage.id"))
