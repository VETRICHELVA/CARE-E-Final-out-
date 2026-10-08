import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity
from app.domain.fulfillment import RouteProvider, ShipmentStatus, StopType


def _one_of(column: str, values: type[ShipmentStatus | StopType | RouteProvider]) -> str:
    return f"{column} IN ({','.join(repr(str(v)) for v in values)})"


class Vehicle(Entity):
    """A logistics org's vehicle. A cold-chain shipment needs one with `has_cold_chain`, and
    a cold-chain product passes matching's cold_chain gate only while one exists (§3)."""

    __tablename__ = "vehicle"
    __table_args__ = (UniqueConstraint("org_id", "reg_no"),)

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    reg_no: Mapped[str] = mapped_column(String(32))
    has_cold_chain: Mapped[bool] = mapped_column(default=False)


class Driver(Entity):
    """A logistics org's driver: the user who signs in to move shipments (DRIVER role)."""

    __tablename__ = "driver"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"), unique=True)
    phone: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(default=True)


class Shipment(Entity):
    """Stock moving from one org to another for a shortage: one per confirmed source request,
    or one per dispatched purchase order (business-rules.md §7 step 5). S09 creates them
    (CREATED); S11 assigns a driver and vehicle (the carrier is the dispatcher's org), stores
    the route and moves them through §8.

    `planned_eta` is the plan's estimate at approval; `eta` is computed from the road route
    at assignment (§4: distance ÷ 40 km/h + 1 h, counted from the assignment).
    `status_history` lists every transition as {from, to, at}."""

    __tablename__ = "shipment"
    __table_args__ = (
        CheckConstraint(_one_of("status", ShipmentStatus), name="status"),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint(
            "(source_request_id IS NULL) <> (purchase_order_id IS NULL)", name="one_origin"
        ),
        CheckConstraint(
            "route_provider IS NULL OR " + _one_of("route_provider", RouteProvider),
            name="route_provider",
        ),
        # Assigned exactly when it has a driver, a vehicle and a carrier.
        CheckConstraint(
            "(status = 'CREATED') = (driver_id IS NULL)"
            " AND (driver_id IS NULL) = (vehicle_id IS NULL)"
            " AND (driver_id IS NULL) = (carrier_org_id IS NULL)",
            name="assignment",
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
    carrier_org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization.id"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product.id"))
    qty: Mapped[int]
    requires_cold_chain: Mapped[bool]
    status: Mapped[str] = mapped_column(String(12), index=True)
    driver_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("driver.id"), index=True)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vehicle.id"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device.id"))
    planned_eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    route_distance_km: Mapped[float | None]
    route_provider: Mapped[str | None] = mapped_column(String(10))
    # none_as_null: clearing the route stores SQL NULL, not the JSON value null.
    route_geometry: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    status_history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )


class ShipmentLeg(Entity):
    """One stop of a shipment's route, set at assignment: seq 1 PICKUP at the source,
    seq 2 DROP at the requesting facility. `actual_at` is when the driver recorded it."""

    __tablename__ = "shipment_leg"
    __table_args__ = (
        UniqueConstraint("shipment_id", "seq"),
        CheckConstraint(_one_of("stop_type", StopType), name="stop_type"),
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipment.id", ondelete="CASCADE"))
    seq: Mapped[int]
    stop_type: Mapped[str] = mapped_column(String(8))
    place: Mapped[str]
    lat: Mapped[float]
    lng: Mapped[float]
    planned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LocationPing(Entity):
    """A position sent by the assigned driver's phone."""

    __tablename__ = "location_ping"
    __table_args__ = (
        CheckConstraint("lat BETWEEN -90 AND 90 AND lng BETWEEN -180 AND 180", name="range"),
        Index("ix_location_ping_shipment_ts", "shipment_id", "ts"),
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipment.id"))
    lat: Mapped[float]
    lng: Mapped[float]
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
