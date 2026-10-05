import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity

QTY_FIELDS = ("on_hand", "reserved", "allocated", "safety_stock", "quarantined")


class InventoryBatch(Entity):
    """Recorded stock. What the network may see is the computed transferable quantity
    (app.domain.inventory), never these raw numbers."""

    __tablename__ = "inventory_batch"
    __table_args__ = (
        UniqueConstraint("facility_id", "product_id", "batch_no"),
        CheckConstraint(
            " AND ".join(f"{f} >= 0" for f in (*QTY_FIELDS, "unit_cost_paise")),
            name="non_negative",
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("facility.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product.id"), index=True)
    batch_no: Mapped[str] = mapped_column(String(64))
    on_hand: Mapped[int]
    reserved: Mapped[int] = mapped_column(default=0)
    allocated: Mapped[int] = mapped_column(default=0)
    safety_stock: Mapped[int] = mapped_column(default=0)
    quarantined: Mapped[int] = mapped_column(default=0)
    expiry_date: Mapped[date]
    unit_cost_paise: Mapped[int]
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VerificationMethod(StrEnum):
    MANUAL = "MANUAL"
    SCAN = "SCAN"


class VerificationEvent(Entity):
    __tablename__ = "verification_event"
    __table_args__ = (
        CheckConstraint("method IN ('MANUAL','SCAN')", name="method"),
        CheckConstraint("counted_qty >= 0", name="counted_qty"),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_batch.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    method: Mapped[str] = mapped_column(String(8))
    counted_qty: Mapped[int]
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
