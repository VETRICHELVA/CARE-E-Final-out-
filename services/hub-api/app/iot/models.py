import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Entity


class DeviceType(StrEnum):
    COLD_BOX = "COLD_BOX"


class Device(Entity):
    """A telemetry device (the ESP32 cold box), owned by the org that carries it.

    `device_id` is the device's own name, as in its MQTT topic `careE/devices/{device_id}/...`.
    Readings from a device_id with no Device row are not stored."""

    __tablename__ = "device"
    __table_args__ = (
        CheckConstraint("type IN ('COLD_BOX')", name="type"),
        CheckConstraint("battery_level BETWEEN 0 AND 100", name="battery_level"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(64), unique=True)
    type: Mapped[str] = mapped_column(String(16), default=DeviceType.COLD_BOX)
    battery_level: Mapped[int | None]
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # S14 part 3 adds `assigned_shipment_id` (FK to shipment) once S11's Shipment exists.


class SensorReading(Entity):
    """One temperature reading. Unique on (device_id, ts), so a replayed message is a no-op."""

    __tablename__ = "sensor_reading"
    __table_args__ = (
        UniqueConstraint("device_id", "ts"),
        CheckConstraint("battery BETWEEN 0 AND 100", name="battery"),
    )

    device_id: Mapped[str] = mapped_column(ForeignKey("device.device_id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    temp_c: Mapped[float]
    battery: Mapped[int | None]
    # S14 part 3 adds `shipment_id`: the device's assigned shipment while it is active.
