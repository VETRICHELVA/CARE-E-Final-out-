import uuid
from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.db import NulFreeStr

# What a device may call itself; it is also an MQTT topic level, so no '/', '+' or '#'.
DeviceName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
MAX_BATCH = 1000
# The same bounds as iot-ingest's Reading, so a reading the ingest accepts is never refused.
TS_MIN, TS_MAX = datetime(2020, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC)


def _in_range(ts: datetime) -> datetime:
    if not TS_MIN <= ts < TS_MAX:
        raise ValueError(f"ts must be from {TS_MIN:%Y-%m-%d} up to {TS_MAX:%Y-%m-%d}")
    return ts


Timestamp = Annotated[AwareDatetime, AfterValidator(_in_range)]


class ReadingIn(BaseModel):
    """One reading as the cold box publishes it (apps-ai-iot.md, Firmware)."""

    model_config = ConfigDict(extra="forbid")

    device_id: DeviceName
    ts: Timestamp
    temp_c: float = Field(ge=-55, le=125, allow_inf_nan=False)  # the DS18B20's range
    battery: int | None = Field(default=None, ge=0, le=100)


class TelemetryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    readings: list[ReadingIn] = Field(max_length=MAX_BATCH)


class TelemetryResult(BaseModel):
    stored: int = Field(description="New readings saved.")
    duplicates: int = Field(description="Readings already stored (same device_id and ts).")
    unknown_devices: list[str] = Field(description="device_ids with no Device; not stored.")


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    device_id: str
    type: str
    battery_level: int | None
    last_seen: datetime | None
    assigned_shipment_id: uuid.UUID | None


class DeviceAssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipment_id: uuid.UUID | None = Field(
        description="The shipment the device rides with; null takes it off its shipment."
    )
    reason: NulFreeStr | None = None
