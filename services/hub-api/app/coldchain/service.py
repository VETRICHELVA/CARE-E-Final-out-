"""Cold-chain rules and events (business-rules.md §11; api-and-events.md, S15).

The hub evaluates the rules, never the firmware or the apps:
- on each ingested batch (`app.iot.service.after_store`), per shipment, in timestamp order:
  EXCURSION after 2 consecutive readings outside the product's band, RECOVERED after 2
  consecutive readings back in range during an excursion;
- every 30 s in the worker (`check_silent_devices`): DEVICE_SILENT when an IN_TRANSIT
  shipment's device has sent no reading for 2 minutes since the later of its last reading
  and the shipment going IN_TRANSIT.

Events are append-only (CLAUDE.md rule 5): a late batch never revises or contradicts one
already recorded (`app.domain.coldchain.on_arrival`).

Each event is a ColdChainEvent row, a SYSTEM audit row stating the readings (or their
absence) as received, and a `coldchain.*` event to the shipment's orgs only. Audit rows go
to the receiving org (the shortage's trail) and are mirrored into the source and carrier
orgs (§10). Nothing here claims what happened to the stock (CLAUDE.md rule 5).

Who reads them (CLAUDE.md rule 6): the shipment's from and to orgs see every reading and
event; its carrier sees those of its own devices only (a box of an earlier carrier is not
its business)."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.auth.models import User
from app.catalog.models import Product
from app.coldchain.models import ColdChainEvent
from app.coldchain.schemas import (
    BandOut,
    ColdChainDeviceOut,
    ColdChainEventOut,
    ColdChainOut,
    ColdChainSummary,
    ReadingOut,
)
from app.domain import coldchain as rules
from app.domain import config
from app.domain.coldchain import Band, ColdChainEventType, Finding, State
from app.domain.events import EventType
from app.domain.fulfillment import ShipmentStatus
from app.errors import AppError
from app.events import service as events
from app.iot.models import Device, SensorReading
from app.shipments import service as shipments
from app.shipments.models import Shipment

ENTITY = "coldchain_event"
E = ColdChainEventType
EVENT_TYPE = {
    E.EXCURSION: EventType.COLDCHAIN_EXCURSION,
    E.DEVICE_SILENT: EventType.COLDCHAIN_DEVICE_SILENT,
    E.RECOVERED: EventType.COLDCHAIN_RECOVERED,
}
READINGS_LIMIT = 2000


def involved_orgs(shipment: Shipment) -> list[uuid.UUID]:
    """Who a `coldchain.*` event goes to (CLAUDE.md rule 6): the shipment's from, to and
    carrier orgs only, never the logistics orgs that see a CREATED shipment on the board."""
    orgs = {shipment.from_org_id, shipment.to_org_id, shipment.carrier_org_id}
    return sorted((o for o in orgs if o is not None), key=str)


async def band_of(session: AsyncSession, shipment: Shipment) -> Band:
    product = await session.get_one(Product, shipment.product_id)
    return Band(product.temp_min_c, product.temp_max_c)


async def _state(session: AsyncSession, shipment_id: uuid.UUID) -> State:
    """The shipment's last EXCURSION or RECOVERED on record (any device: §11 runs per
    shipment). They alternate and are recorded in timestamp order, so the newest by `ts` is
    the last one recorded."""
    last = await session.scalar(
        select(ColdChainEvent)
        .where(
            ColdChainEvent.shipment_id == shipment_id,
            ColdChainEvent.type.in_([E.EXCURSION, E.RECOVERED]),
        )
        .order_by(ColdChainEvent.ts.desc(), ColdChainEvent.created_at.desc())
        .limit(1)
    )
    if last is None:
        return State()
    if last.type != E.EXCURSION:
        return State(as_of=last.ts)
    return State(in_excursion=True, excursion_threshold=last.threshold, as_of=last.ts)


async def evaluate_readings(
    session: AsyncSession, shipment: Shipment, device: Device, stored: Sequence[SensorReading]
) -> list[ColdChainEvent]:
    """§11 over readings just linked to `shipment`: every reading of the shipment from
    `rules.resume_from` on, merged with the new ones in timestamp order, carrying on from
    the shipment's last EXCURSION or RECOVERED on record (`rules.on_arrival` states what a
    late batch can and cannot change). Events on record are never revised.

    Only EXCURSION and RECOVERED set where evaluation resumes: a DEVICE_SILENT does not, so
    readings a box buffered while out of reach still count when they arrive. The caller
    holds the device's row lock, and a shipment carries one device at a time, so batches
    for a shipment never interleave."""
    if not stored:
        return []
    band = await band_of(session, shipment)
    if not band.defined:
        return []
    state = await _state(session, shipment.id)
    start, inclusive = rules.resume_from(state, min(r.ts for r in stored))
    pending = SensorReading.ts >= start if inclusive else SensorReading.ts > start
    of_shipment = SensorReading.shipment_id == shipment.id
    order = (SensorReading.ts, SensorReading.device_id)
    tail = list(
        await session.scalars(select(SensorReading).where(of_shipment, pending).order_by(*order))
    )
    keep = max(config.COLDCHAIN_CONSECUTIVE_READINGS - 1, 0)
    lead_in = list(
        await session.scalars(
            select(SensorReading)
            .where(of_shipment, ~pending)
            .order_by(*(c.desc() for c in order))
            .limit(keep)
        )
    )
    findings = rules.on_arrival(
        band,
        [rules.Reading(r.ts, r.temp_c) for r in [*reversed(lead_in), *tail]],
        [rules.Reading(r.ts, r.temp_c) for r in stored],
        state,
    )
    return [await record(session, shipment, device.device_id, f, band) for f in findings]


async def record(
    session: AsyncSession,
    shipment: Shipment,
    device_id: str,
    finding: Finding,
    band: Band,
    *,
    since: datetime | None = None,
    last_reading_at: datetime | None = None,
    in_transit_at: datetime | None = None,
) -> ColdChainEvent:
    """Store one cold-chain event, its SYSTEM audit rows and its `coldchain.*` event. A
    DEVICE_SILENT takes `since` (when the silence counts from), the device's
    `last_reading_at` and the shipment's `in_transit_at`."""
    event = ColdChainEvent(
        id=uuid.uuid4(),
        shipment_id=shipment.id,
        device_id=device_id,
        type=finding.type,
        threshold=finding.threshold,
        observed_value=finding.observed_value,
        severity=finding.severity,
        ts=finding.ts,
    )
    session.add(event)
    await session.flush()
    after: dict[str, Any] = {
        "shipment_id": shipment.id,
        "device_id": device_id,
        "type": finding.type,
        "severity": finding.severity,
        "observed_value": finding.observed_value,
        "threshold": finding.threshold,
        "ts": finding.ts,
    }
    if finding.type == E.DEVICE_SILENT:
        after["unit"] = "s"
        after["silent_since"] = since
        after["last_reading_at"] = last_reading_at
        after["in_transit_at"] = in_transit_at
    else:
        after["unit"] = "°C"
        after["band"] = {"temp_min_c": band.min_c, "temp_max_c": band.max_c}
        after["readings"] = [{"ts": r.ts, "temp_c": r.temp_c} for r in finding.readings]
    row = await audit.record(
        session,
        None,
        ENTITY,
        event.id,
        f"{ENTITY}.{finding.type.lower()}",
        None,
        after,
        rules.describe(finding, band, device_id, since, in_transit_at=in_transit_at),
        org_id=shipment.to_org_id,
    )
    for org_id in {shipment.from_org_id, shipment.carrier_org_id} - {None, shipment.to_org_id}:
        assert org_id is not None
        await audit.mirror(session, row, org_id)
    await events.emit(
        session,
        EVENT_TYPE[finding.type],
        involved_orgs(shipment),
        {
            "shipment_id": shipment.id,
            "coldchain_event_id": event.id,
            "device_id": device_id,
            "severity": finding.severity,
            "observed_value": finding.observed_value,
            "threshold": finding.threshold,
            "ts": finding.ts,
            "to_org_id": shipment.to_org_id,
        },
    )
    return event


# --- DEVICE_SILENT (worker) -------------------------------------------------------------------


def _in_transit_at(shipment: Shipment) -> datetime | None:
    moves = [h for h in shipment.status_history or [] if h.get("to") == ShipmentStatus.IN_TRANSIT]
    return datetime.fromisoformat(moves[-1]["at"]) if moves else None


async def check_silent_devices(session: AsyncSession, *, now: datetime | None = None) -> int:
    """§11: DEVICE_SILENT for each IN_TRANSIT shipment whose device has sent no reading for
    2 minutes while the shipment was IN_TRANSIT: counted from the later of the device's
    `last_seen` and the recorded time the shipment went IN_TRANSIT (its status history).
    Once per silence: a device that sends again and then falls silent again raises a new
    one. Commits; returns how many were raised."""
    now = now or datetime.now(UTC)
    rows = (
        await session.execute(
            select(Shipment, Device)
            .join(Device, Device.id == Shipment.device_id)
            .where(Shipment.status == ShipmentStatus.IN_TRANSIT)
            .order_by(Shipment.id)
            .with_for_update(of=Shipment, skip_locked=True)
        )
    ).all()
    raised = 0
    for shipment, device in rows:
        in_transit_at = _in_transit_at(shipment)
        since = rules.silence_start(device.last_seen, in_transit_at)
        last_silent = await session.scalar(
            select(ColdChainEvent.ts)
            .where(
                ColdChainEvent.shipment_id == shipment.id,
                ColdChainEvent.device_id == device.device_id,
                ColdChainEvent.type == E.DEVICE_SILENT,
            )
            .order_by(ColdChainEvent.ts.desc())
            .limit(1)
        )
        finding = rules.silent_finding(since, now, last_silent)
        if finding is None:
            continue
        band = await band_of(session, shipment)
        await record(
            session,
            shipment,
            device.device_id,
            finding,
            band,
            since=since,
            last_reading_at=device.last_seen,
            in_transit_at=in_transit_at,
        )
        raised += 1
    await session.commit()
    return raised


# --- reads ------------------------------------------------------------------------------------


def _own_devices_only(user: User, shipment: Shipment) -> bool:
    """The carrier (not also the sender or receiver) sees only its own devices' data."""
    return user.org_id not in (shipment.from_org_id, shipment.to_org_id)


def _visible(column: Any, user: User, shipment: Shipment) -> Any:
    """A where-clause on a `device_id` column: every device for the from and to orgs, the
    carrier's own devices only for the carrier."""
    if _own_devices_only(user, shipment):
        return column.in_(select(Device.device_id).where(Device.org_id == user.org_id))
    return true()


async def get_involved(session: AsyncSession, user: User, shipment_id: uuid.UUID) -> Shipment:
    """404 if unknown; 403 unless the caller's org is the shipment's from, to or carrier org
    (a logistics org that sees it only on the dispatch board is not involved)."""
    shipment = await shipments.get_visible(session, user, shipment_id)
    if user.org_id not in (shipment.from_org_id, shipment.to_org_id, shipment.carrier_org_id):
        raise AppError(403, "forbidden", "Only the shipment's organizations see its cold chain.")
    return shipment


async def view(
    session: AsyncSession, user: User, shipment_id: uuid.UUID, limit: int = READINGS_LIMIT
) -> ColdChainOut:
    shipment = await get_involved(session, user, shipment_id)
    band = await band_of(session, shipment)
    newest = list(
        await session.scalars(
            select(SensorReading)
            .where(
                SensorReading.shipment_id == shipment.id,
                _visible(SensorReading.device_id, user, shipment),
            )
            .order_by(SensorReading.ts.desc(), SensorReading.device_id.desc())
            .limit(limit)
        )
    )
    found = list(
        await session.scalars(
            select(ColdChainEvent)
            .where(
                ColdChainEvent.shipment_id == shipment.id,
                _visible(ColdChainEvent.device_id, user, shipment),
            )
            .order_by(ColdChainEvent.ts, ColdChainEvent.created_at)
        )
    )
    device = await _device_for(session, user, shipment, newest)
    return ColdChainOut(
        shipment_id=shipment.id,
        requires_cold_chain=shipment.requires_cold_chain,
        band=BandOut(temp_min_c=band.min_c, temp_max_c=band.max_c),
        device=ColdChainDeviceOut.model_validate(device) if device else None,
        silent_after_seconds=int(config.COLDCHAIN_SILENT_AFTER.total_seconds()),
        readings=[ReadingOut.model_validate(r) for r in reversed(newest)],
        events=[ColdChainEventOut.model_validate(e) for e in found],
        has_excursion=any(e.type == E.EXCURSION for e in found),
    )


async def _device_for(
    session: AsyncSession, user: User, shipment: Shipment, readings: Sequence[SensorReading]
) -> Device | None:
    """The box on the shipment now or, once it is off, the one that sent the newest reading
    the caller may see."""
    device = await session.get(Device, shipment.device_id) if shipment.device_id else None
    if device is None and readings:
        device = await session.scalar(
            select(Device).where(Device.device_id == readings[0].device_id)
        )
    if device is not None and _own_devices_only(user, shipment) and device.org_id != user.org_id:
        return None
    return device


async def summaries(
    session: AsyncSession, user: User, rows: Sequence[Shipment]
) -> dict[uuid.UUID, ColdChainSummary]:
    """For shipment lists: each shipment's newest cold-chain event the caller may see, and
    whether an excursion is on record. Shipments without one are left out."""
    involved = [s for s in rows if user.org_id in (s.from_org_id, s.to_org_id, s.carrier_org_id)]
    if not involved:
        return {}
    stmt = (
        select(ColdChainEvent, Device.org_id)
        .join(Device, Device.device_id == ColdChainEvent.device_id)
        .where(ColdChainEvent.shipment_id.in_([s.id for s in involved]))
        .order_by(ColdChainEvent.ts, ColdChainEvent.created_at)
    )
    by_id = {s.id: s for s in involved}
    seen: dict[uuid.UUID, list[ColdChainEvent]] = {}
    for event, device_org in await session.execute(stmt):
        shipment = by_id[event.shipment_id]
        if _own_devices_only(user, shipment) and device_org != user.org_id:
            continue
        seen.setdefault(event.shipment_id, []).append(event)
    return {
        shipment_id: ColdChainSummary(
            last_event_type=ColdChainEventType(found[-1].type),
            last_event_at=found[-1].ts,
            had_excursion=any(e.type == E.EXCURSION for e in found),
        )
        for shipment_id, found in seen.items()
    }
