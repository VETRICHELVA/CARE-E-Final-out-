import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.coldchain import ColdChainEventType, Severity


class BandOut(BaseModel):
    temp_min_c: float | None
    temp_max_c: float | None


class ReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: str
    ts: datetime
    temp_c: float
    battery: int | None


class ColdChainEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: ColdChainEventType
    severity: Severity
    device_id: str
    observed_value: float = Field(
        description="EXCURSION / RECOVERED: the reading (°C) that completed the run of "
        "consecutive readings. DEVICE_SILENT: seconds without a reading when it was noticed."
    )
    threshold: float = Field(
        description="EXCURSION / RECOVERED: the band's bound (°C) the excursion crossed. "
        "DEVICE_SILENT: the silence limit in seconds."
    )
    ts: datetime = Field(
        description="The reading that completed the run, or when the silence was noticed."
    )


class ColdChainDeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: str
    battery_level: int | None
    last_seen: datetime | None


class ColdChainOut(BaseModel):
    shipment_id: uuid.UUID
    requires_cold_chain: bool
    band: BandOut = Field(description="The product's allowed range; both null: no rule.")
    device: ColdChainDeviceOut | None = Field(
        description="The cold box on the shipment, or the one that sent its newest reading."
    )
    silent_after_seconds: int = Field(
        description="A device silent this long while IN_TRANSIT raises DEVICE_SILENT."
    )
    readings: list[ReadingOut] = Field(description="The newest readings, oldest first.")
    events: list[ColdChainEventOut] = Field(description="Every cold-chain event, oldest first.")
    has_excursion: bool = Field(description="An EXCURSION is on record (it stays after RECOVERED).")


class ColdChainSummary(BaseModel):
    """A shipment's cold-chain state for lists (the dispatch board and Deliveries badges)."""

    last_event_type: ColdChainEventType
    last_event_at: datetime
    had_excursion: bool
