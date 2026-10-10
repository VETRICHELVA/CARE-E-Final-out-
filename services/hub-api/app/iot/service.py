"""Telemetry ingest (apps-ai-iot.md, Ingest): store readings once, keep Device fresh, link
them to the device's shipment and run the cold-chain rules (business-rules.md §11, in
`app.coldchain.service`) on them."""

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.models import User
from app.coldchain import service as coldchain
from app.domain.events import EventType
from app.domain.fulfillment import ACTIVE_SHIPMENT, ARRIVED
from app.errors import AppError
from app.events import service as events
from app.iot.models import Device, SensorReading
from app.iot.schemas import ReadingIn, TelemetryBatch, TelemetryResult
from app.shipments import service as shipments
from app.shipments.models import Shipment

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
    """Hook for each device's newly stored readings (none on a replay): link them to the
    device's assigned shipment while that shipment is ASSIGNED, PICKED_UP or IN_TRANSIT, and
    emit `coldchain.reading` {shipment_id, temp_c, ts} for each linked reading to the
    shipment's orgs, then evaluate the cold-chain rules on them (S15). Readings of an
    unassigned device, or of a shipment not on its way, stay unlinked and emit nothing."""
    if not stored or device.assigned_shipment_id is None:
        return
    shipment = await session.get(Shipment, device.assigned_shipment_id)
    if shipment is None or shipment.status not in ACTIVE_SHIPMENT:
        return
    org_ids = await shipments.audience(session, shipment)
    for reading in sorted(stored, key=lambda r: r.ts):
        reading.shipment_id = shipment.id
        await events.emit(
            session,
            EventType.COLDCHAIN_READING,
            org_ids,
            {"shipment_id": shipment.id, "temp_c": reading.temp_c, "ts": reading.ts},
        )
    await session.flush()
    await coldchain.evaluate_readings(session, shipment, device, stored)


# --- device assignment (S14 part 3) -----------------------------------------------------------

DEVICE = "device"


async def own_device(session: AsyncSession, user: User, device_pk: uuid.UUID) -> Device:
    device = await session.get(Device, device_pk, with_for_update=True, populate_existing=True)
    if device is None:
        raise AppError(404, "not_found", "Device not found.")
    if device.org_id != user.org_id:
        raise AppError(403, "forbidden", "This device belongs to another organization.")
    return device


async def assign_device(
    session: AsyncSession,
    user: User,
    device_pk: uuid.UUID,
    shipment_id: uuid.UUID | None,
    reason: str | None,
) -> Device:
    """Put the caller's org's device on a shipment its org carries, or take it off
    (`shipment_id` null). A box rides only with its own org's shipments (CLAUDE.md rule 6):
    403 when another org carries the shipment, 409 while it is CREATED (no carrier yet;
    assigning or unassigning it takes any device off). The shipment must not have arrived
    (409), and must not carry another device (409: take that one off first). One audit row
    in the device's org."""
    device = await own_device(session, user, device_pk)
    before = device.assigned_shipment_id
    target: Shipment | None = None
    if shipment_id is not None:
        target = await shipments.get_visible(session, user, shipment_id, lock=True)
        if target.carrier_org_id is None:
            raise AppError(
                409,
                "conflict",
                "The shipment has no carrier yet; a device goes on once it is assigned.",
                {"status": target.status},
            )
        if target.carrier_org_id != device.org_id:
            raise AppError(403, "forbidden", "Another organization carries this shipment.")
        if target.status in ARRIVED:
            raise AppError(
                409,
                "conflict",
                f"The shipment is {target.status}; a device can no longer be attached.",
                {"status": target.status},
            )
        if target.device_id not in (None, device.id):
            raise AppError(
                409,
                "conflict",
                "Another device is on this shipment; take it off first.",
                {"device_id": target.device_id},
            )
    if before == shipment_id:
        return device
    if before is not None:
        previous = await session.get(Shipment, before, with_for_update=True)
        if previous is not None and previous.device_id == device.id:
            previous.device_id = None
    device.assigned_shipment_id = shipment_id
    if target is not None:
        target.device_id = device.id
    await session.flush()
    action = f"{DEVICE}.assigned" if shipment_id else f"{DEVICE}.unassigned"
    await audit.record(
        session,
        user,
        DEVICE,
        device.id,
        action,
        {"assigned_shipment_id": before},
        {"assigned_shipment_id": shipment_id},
        reason,
    )
    return device
