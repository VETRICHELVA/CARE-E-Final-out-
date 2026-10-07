"""Telemetry ingest (apps-ai-iot.md, Ingest): store readings once, keep Device fresh.

Cold-chain rules (business-rules.md §11) are S15's and run in the hub, not here."""

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.iot.models import Device, SensorReading
from app.iot.schemas import ReadingIn, TelemetryBatch, TelemetryResult

log = logging.getLogger("app")


async def ingest(session: AsyncSession, batch: TelemetryBatch) -> TelemetryResult:
    """Insert each reading of a known device unless (device_id, ts) is already stored, so
    replaying a batch changes nothing. Then advance each device's last_seen and battery."""
    names = sorted({r.device_id for r in batch.readings})
    # Lock in name order: two batches for the same devices serialize without deadlocking.
    devices = {
        d.device_id: d
        for d in await session.scalars(
            select(Device)
            .where(Device.device_id.in_(names))
            .order_by(Device.device_id)
            .with_for_update()
        )
    }
    unknown = [n for n in names if n not in devices]
    if unknown:
        log.warning("telemetry from unregistered devices", extra={"device_ids": unknown})

    known = [r for r in batch.readings if r.device_id in devices]
    stored: list[SensorReading] = []
    if known:
        rows = [
            {
                "id": uuid.uuid4(),
                "device_id": r.device_id,
                "ts": r.ts,
                "temp_c": r.temp_c,
                "battery": r.battery,
            }
            for r in known
        ]
        stmt = (
            insert(SensorReading)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["device_id", "ts"])
            .returning(SensorReading)
        )
        stored = list(await session.scalars(stmt))

    now = datetime.now(UTC)
    for name, device in devices.items():
        refresh_device(device, [r for r in known if r.device_id == name], now)
        await after_store(session, device, [s for s in stored if s.device_id == name])
    await session.flush()
    return TelemetryResult(
        stored=len(stored), duplicates=len(known) - len(stored), unknown_devices=unknown
    )


def refresh_device(device: Device, readings: Sequence[ReadingIn], now: datetime) -> None:
    """last_seen moves only forward (late, buffered readings don't rewind it) and never past
    `now` (a fast device clock can't hide a silent device). Battery comes from the newest
    reading that has one, unless the device was already seen later than that reading."""
    if not readings:
        return
    previous = device.last_seen
    newest = min(max(r.ts for r in readings), now)
    if previous is None or newest > previous:
        device.last_seen = newest
    with_battery = [r for r in readings if r.battery is not None]
    if with_battery:
        latest = max(with_battery, key=lambda r: r.ts)
        if previous is None or min(latest.ts, now) >= previous:
            device.battery_level = latest.battery


async def after_store(
    session: AsyncSession, device: Device, stored: Sequence[SensorReading]
) -> None:
    """Hook for each device's newly stored readings (none on a replay). Still to come:
    - S14 part 3 (needs S11 Shipment): set `reading.shipment_id = device.assigned_shipment_id`
      only while that shipment is ASSIGNED, PICKED_UP or IN_TRANSIT.
    - S07: emit `coldchain.reading` {shipment_id, temp_c, ts} for each linked reading.
    - S15: evaluate the cold-chain rules on the linked readings."""
