"""Turning an MQTT message into a validated reading (apps-ai-iot.md, Firmware)."""

import json
import logging
import re
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, ValidationError

log = logging.getLogger("iot_ingest")

TOPIC_FILTER = "careE/devices/+/telemetry"
TOPIC = re.compile(r"^careE/devices/([^/]+)/telemetry$")

# The same limits as the hub's POST /internal/telemetry, so a valid reading is never refused.
DeviceName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class Reading(BaseModel):
    """`{"device_id":"cb-01","ts":"2026-11-20T10:15:00Z","temp_c":4.31,"battery":82}`;
    battery is omitted when the box has no battery ADC wired."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    device_id: DeviceName
    ts: AwareDatetime
    temp_c: float = Field(ge=-55, le=125, allow_inf_nan=False)  # the DS18B20's range
    battery: int | None = Field(default=None, ge=0, le=100)

    @property
    def key(self) -> tuple[str, AwareDatetime]:
        return (self.device_id, self.ts)

    def to_hub(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude_none=True)


def parse(topic: str, payload: bytes) -> Reading | None:
    """The reading in a telemetry message, or None (logged) if the message is not one.
    The payload's device_id must match the topic's."""
    match = TOPIC.match(topic)
    if match is None:
        log.warning("ignored message on unexpected topic %s", topic)
        return None
    try:
        reading = Reading.model_validate(json.loads(payload))
    except (ValueError, ValidationError) as e:  # bad UTF-8 or JSON, or an invalid reading
        log.warning("invalid telemetry on %s: %s", topic, e)
        return None
    if reading.device_id != match.group(1):
        log.warning("device_id %s does not match topic %s", reading.device_id, topic)
        return None
    return reading
