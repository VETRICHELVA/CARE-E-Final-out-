import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity
from app.domain.fulfillment import ShipmentStatus


class Shipment(Entity):
    """Stock moving from one org to another for a shortage: one per confirmed source request,
    or one per dispatched purchase order (business-rules.md §7 step 5). S09 creates them
    (CREATED); S11 adds assignment and movement, and the driver and vehicle tables that
    `driver_id` and `vehicle_id` will reference."""

    __tablename__ = "shipment"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({','.join(repr(str(v)) for v in ShipmentStatus)})", name="status"
        ),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint(
            "(source_request_id IS NULL) <> (purchase_order_id IS NULL)", name="one_origin"
        ),
    )

    shortage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shortage.id"), index=True)
    source_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_request.id"), unique=True
    )
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_order.id"), unique=True
    )
    from_org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    to_org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product.id"))
    qty: Mapped[int]
    requires_cold_chain: Mapped[bool]
    status: Mapped[str] = mapped_column(String(12))
    driver_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device.id"))
    planned_eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    route_geometry: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
