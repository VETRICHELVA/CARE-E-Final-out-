"""Shipments (business-rules.md §4, §7 step 5, §8, §9). S09 creates them (CREATED); S11
assigns a driver and vehicle with a road route and ETA, lets the assigned driver move them
through §8 (drawing down the source's stock and FIRM hold at pickup), and stores the
driver's location pings.

Who sees a shipment: its from, to and carrier orgs, and, while it is CREATED (unassigned,
on the dispatch board), every LOGISTICS org. Events go to the same orgs.

Audit (§10): each transition is one row in the acting user's org, mirrored into the
shortage's org (its trail) without the actor's id when that is another org; a system change
goes to the shortage's org. The source's stock and hold changes at pickup are SYSTEM rows
in the source org, and a device taken off because the carrier changed is a SYSTEM row in
the device's org.

A cold box rides only with its own org's shipments (CLAUDE.md rule 6): a device goes on a
shipment only while its org is the carrier (`app.iot.service.assign_device`), and assign
and unassign take any device off, since they change the carrier. The detail shows only the
location pings recorded since the current assignment."""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import routing
from app.audit import service as audit
from app.audit.models import AuditLog
from app.auth.models import User
from app.catalog.models import Product
from app.domain.costing import Point, geojson_line, transport_eta_hours
from app.domain.events import EventType
from app.domain.fulfillment import (
    ACTIVE_SHIPMENT,
    CARRIER_CHANGED,
    MULTI_FACILITY,
    SHIPMENT_TRANSITIONS,
    ShipmentStatus,
    StopType,
    cold_chain_vehicle_refusal,
    driver_may_move,
    short_pickup,
)
from app.domain.source_request import HoldStatus
from app.domain.state_machine import InvalidTransition, transition
from app.errors import AppError
from app.events import service as events
from app.inventory.models import InventoryBatch
from app.iot.models import Device
from app.orgs.models import Facility, Organization, OrgType
from app.shipments.models import Driver, LocationPing, Shipment, ShipmentLeg, Vehicle
from app.shortages.models import Shortage
from app.source_requests import holds
from app.source_requests.models import Hold

ENTITY = "shipment"
BATCH = "inventory_batch"
DEVICE = "device"
S = ShipmentStatus


# --- who sees a shipment, and who hears about it ----------------------------------------------


def _is_logistics(user: User) -> bool:
    return bool(user.org.type == OrgType.LOGISTICS)


def can_see(user: User, shipment: Shipment) -> bool:
    if user.org_id in (shipment.from_org_id, shipment.to_org_id, shipment.carrier_org_id):
        return True
    return shipment.status == S.CREATED and _is_logistics(user)


def visible_to(stmt: Select[Any], user: User) -> Select[Any]:
    """Limit a select of Shipment to the ones `user`'s org may see (`can_see`)."""
    involved = or_(
        Shipment.from_org_id == user.org_id,
        Shipment.to_org_id == user.org_id,
        Shipment.carrier_org_id == user.org_id,
    )
    if _is_logistics(user):
        involved = or_(involved, Shipment.status == S.CREATED)
    return stmt.where(involved)


async def audience(
    session: AsyncSession, shipment: Shipment, *also: uuid.UUID | None
) -> list[uuid.UUID]:
    """The orgs an event about `shipment` goes to: from, to, carrier, any `also` (e.g. the
    carrier it just left) and, while it is CREATED, every LOGISTICS org."""
    orgs = {shipment.from_org_id, shipment.to_org_id, shipment.carrier_org_id, *also}
    if shipment.status == S.CREATED:
        orgs.update(
            await session.scalars(
                select(Organization.id).where(Organization.type == OrgType.LOGISTICS)
            )
        )
    return sorted((o for o in orgs if o is not None), key=str)


async def get_visible(
    session: AsyncSession, user: User, shipment_id: uuid.UUID, *, lock: bool = False
) -> Shipment:
    """404 if unknown, 403 if the caller's org is not involved; `lock` takes FOR UPDATE."""
    shipment = await session.get(
        Shipment, shipment_id, with_for_update=lock, populate_existing=lock
    )
    if shipment is None:
        raise AppError(404, "not_found", "Shipment not found.")
    if not can_see(user, shipment):
        raise AppError(403, "forbidden", "This shipment belongs to other organizations.")
    return shipment


# --- creation (S09) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stop:
    place: str
    point: Point


async def _pickup(session: AsyncSession, shipment: Shipment) -> Stop:
    """Where the stock is: for a transfer, the facility holding the largest share of the
    request's held stock (one pickup per shipment: assign refuses held stock at several
    facilities until S16's multi-stop planner); for a purchase
    order, the supplier's location (as matching measures it)."""
    if shipment.source_request_id is not None:
        row = (
            await session.execute(
                select(Facility.name, Facility.lat, Facility.lng)
                .join(InventoryBatch, InventoryBatch.facility_id == Facility.id)
                .join(Hold, Hold.batch_id == InventoryBatch.id)
                .where(Hold.source_request_id == shipment.source_request_id)
                .order_by(Hold.qty.desc(), InventoryBatch.id)
                .limit(1)
            )
        ).first()
        if row is not None:
            return Stop(row.name, Point(row.lat, row.lng))
    org = await session.get_one(Organization, shipment.from_org_id)
    return Stop(org.name, Point(org.lat, org.lng))


async def _drop(session: AsyncSession, shipment: Shipment) -> Stop:
    shortage = await session.get_one(Shortage, shipment.shortage_id)
    facility = await session.get_one(Facility, shortage.facility_id)
    return Stop(facility.name, Point(facility.lat, facility.lng))


async def legs_of(session: AsyncSession, shipment_id: uuid.UUID) -> list[ShipmentLeg]:
    stmt = select(ShipmentLeg).where(ShipmentLeg.shipment_id == shipment_id)
    return list(await session.scalars(stmt.order_by(ShipmentLeg.seq)))


async def _ensure_legs(session: AsyncSession, shipment: Shipment) -> list[ShipmentLeg]:
    """PICKUP (seq 1) and DROP (seq 2); added at creation, or now for an older shipment."""
    legs = await legs_of(session, shipment.id)
    if legs:
        return legs
    pickup, drop = await _pickup(session, shipment), await _drop(session, shipment)
    legs = [
        ShipmentLeg(
            shipment_id=shipment.id,
            seq=n,
            stop_type=kind,
            place=stop.place,
            lat=stop.point.lat,
            lng=stop.point.lng,
            planned_at=shipment.planned_eta if kind == StopType.DROP else None,
        )
        for n, (kind, stop) in enumerate(((StopType.PICKUP, pickup), (StopType.DROP, drop)), 1)
    ]
    session.add_all(legs)
    await session.flush()
    return legs


async def stops_of(session: AsyncSession, shipment: Shipment) -> tuple[Stop, Stop]:
    """The shipment's pickup and drop: its stored legs, or where they would be (an older
    shipment without legs gets them at assignment). Writes nothing."""
    legs = {leg.stop_type: leg for leg in await legs_of(session, shipment.id)}
    if StopType.PICKUP in legs and StopType.DROP in legs:
        pickup, drop = legs[StopType.PICKUP], legs[StopType.DROP]
        return (
            Stop(pickup.place, Point(pickup.lat, pickup.lng)),
            Stop(drop.place, Point(drop.lat, drop.lng)),
        )
    return await _pickup(session, shipment), await _drop(session, shipment)


def _history(shipment: Shipment, before: str | None, at: datetime) -> None:
    # A new list, so SQLAlchemy sees the JSONB change.
    shipment.status_history = [
        *(shipment.status_history or []),
        {"from": before, "to": shipment.status, "at": at.isoformat()},
    ]


async def create(
    session: AsyncSession,
    shortage: Shortage,
    *,
    from_org_id: uuid.UUID,
    qty: int,
    planned_eta: datetime | None,
    actor: User | None,
    reason: str | None,
    source_request_id: uuid.UUID | None = None,
    purchase_order_id: uuid.UUID | None = None,
) -> Shipment:
    """One CREATED shipment from `from_org_id` to the shortage's org, for one confirmed
    source request or one dispatched purchase order, with its PICKUP and DROP stops. Its
    audit row goes to the acting user's org (the requester's on approval; on dispatch the
    supplier's, mirrored into the requester's trail without the user's id);
    `shipment.created` goes to both orgs and, since it waits for a carrier, to every
    LOGISTICS org."""
    product = await session.get_one(Product, shortage.product_id)
    shipment = Shipment(
        id=uuid.uuid4(),
        shortage_id=shortage.id,
        source_request_id=source_request_id,
        purchase_order_id=purchase_order_id,
        from_org_id=from_org_id,
        to_org_id=shortage.org_id,
        product_id=shortage.product_id,
        qty=qty,
        requires_cold_chain=product.requires_cold_chain,
        status=S.CREATED,
        planned_eta=planned_eta,
        status_history=[],
    )
    _history(shipment, None, datetime.now(UTC))
    session.add(shipment)
    await session.flush()
    await _ensure_legs(session, shipment)
    after = {
        "status": shipment.status,
        "shortage_id": shortage.id,
        "source_request_id": source_request_id,
        "purchase_order_id": purchase_order_id,
        "from_org_id": from_org_id,
        "to_org_id": shortage.org_id,
        "qty": qty,
        "requires_cold_chain": shipment.requires_cold_chain,
        "planned_eta": planned_eta,
    }
    row = await audit.record(
        session, actor, ENTITY, shipment.id, f"{ENTITY}.created", None, after, reason,
        org_id=actor.org_id if actor else shortage.org_id,
    )  # fmt: skip
    if actor is not None and actor.org_id != shortage.org_id:
        await audit.mirror(session, row, shortage.org_id)
    await events.emit(
        session,
        EventType.SHIPMENT_CREATED,
        await audience(session, shipment),
        {"shipment_id": shipment.id, "from": None, "to": shipment.status},
    )
    return shipment


# --- transitions ------------------------------------------------------------------------------


async def _move(
    session: AsyncSession,
    shipment: Shipment,
    to: ShipmentStatus,
    actor: User,
    reason: str | None,
    now: datetime,
    *,
    changes: dict[str, Any],
    notify_also: Iterable[uuid.UUID | None] = (),
) -> str:
    """Every S11 transition: §8 state machine (409 if not allowed), the status history,
    one audit row in the actor's org (mirrored into the shortage's org) and one event."""
    before = transition(shipment, to, SHIPMENT_TRANSITIONS)
    _history(shipment, before, now)
    await session.flush()
    row = await audit.record(
        session,
        actor,
        ENTITY,
        shipment.id,
        f"{ENTITY}.status_changed",
        {"status": before},
        {"status": shipment.status, **changes},
        reason,
    )
    if actor.org_id != shipment.to_org_id:
        await audit.mirror(session, row, shipment.to_org_id)
    await events.emit(
        session,
        EventType.SHIPMENT_STATUS_CHANGED,
        await audience(session, shipment, *notify_also),
        {"shipment_id": shipment.id, "from": before, "to": shipment.status},
    )
    return before


def _check(shipment: Shipment, to: ShipmentStatus, allowed: bool) -> None:
    if not allowed:
        raise InvalidTransition(shipment.status, to)


async def _own[T: (Driver, Vehicle)](
    session: AsyncSession, user: User, model: type[T], obj_id: uuid.UUID
) -> T:
    obj = await session.get(model, obj_id)
    noun = model.__name__
    if obj is None:
        raise AppError(404, "not_found", f"{noun} not found.")
    if obj.org_id != user.org_id:
        raise AppError(403, "forbidden", f"This {noun.lower()} belongs to another organization.")
    return obj


async def own_driver(session: AsyncSession, user: User, driver_id: uuid.UUID) -> Driver:
    """One of the caller's org's active drivers: 404 unknown, 403 another org's, 400
    inactive (the checks `assign` makes)."""
    driver: Driver = await _own(session, user, Driver, driver_id)
    if not driver.active:
        raise AppError(400, "validation", "This driver is not active.", {"driver_id": driver_id})
    return driver


async def own_vehicle(session: AsyncSession, user: User, vehicle_id: uuid.UUID) -> Vehicle:
    """One of the caller's org's vehicles: 404 unknown, 403 another org's."""
    vehicle: Vehicle = await _own(session, user, Vehicle, vehicle_id)
    return vehicle


async def _pickup_facilities(session: AsyncSession, shipment: Shipment) -> int:
    """How many of the source's facilities hold the shipment's FIRM holds."""
    if shipment.source_request_id is None:
        return 1  # a purchase order: one pickup at the supplier
    count = await session.scalar(
        select(func.count(func.distinct(InventoryBatch.facility_id)))
        .select_from(Hold)
        .join(InventoryBatch, InventoryBatch.id == Hold.batch_id)
        .where(
            Hold.source_request_id == shipment.source_request_id,
            Hold.status == HoldStatus.FIRM,
        )
    )
    return int(count or 0)


async def _take_off_device(session: AsyncSession, shipment: Shipment) -> uuid.UUID | None:
    """The carrier is changing: take any device off the shipment (a box rides only with its
    own org's shipments; CLAUDE.md rule 6). The device's row is SYSTEM in the device's org
    with the factual cause. Returns the device's id, or None if none was on it."""
    if shipment.device_id is None:
        return None
    device = await session.get(Device, shipment.device_id, populate_existing=True)
    shipment.device_id = None
    if device is None:
        return None
    if device.assigned_shipment_id == shipment.id:
        device.assigned_shipment_id = None
        await session.flush()
        await audit.record(
            session,
            None,
            DEVICE,
            device.id,
            f"{DEVICE}.unassigned",
            {"assigned_shipment_id": shipment.id},
            {"assigned_shipment_id": None},
            CARRIER_CHANGED,
            org_id=device.org_id,
        )
    return device.id


async def assign(
    session: AsyncSession,
    user: User,
    shipment_id: uuid.UUID,
    *,
    driver_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    reason: str | None,
    now: datetime | None = None,
    planned: tuple[datetime, datetime] | None = None,
) -> Shipment:
    """CREATED -> ASSIGNED with the caller's org's active driver and vehicle; the caller's
    org becomes the carrier. A cold-chain shipment needs a cold-chain vehicle (400 with the
    reason). A transfer whose FIRM holds sit at more than one of the source's facilities is
    a 409 `conflict` (§8: one pickup per shipment until the route planner supports
    multi-stop pickups). Any device on the shipment comes off (the carrier changes). The
    road route (OSRM, or haversine when OSRM is off, failing or slow) gives the distance,
    the geometry and the ETA: now + distance ÷ 40 km/h + 1 h (§4).

    `planned` is (pickup, drop) from a route plan (S16, several shipments for one driver):
    the legs' `planned_at` and the ETA then come from the plan instead."""
    now = now or datetime.now(UTC)
    shipment = await get_visible(session, user, shipment_id, lock=True)
    _check(shipment, S.ASSIGNED, S.ASSIGNED in SHIPMENT_TRANSITIONS.get(shipment.status, set()))
    facilities = await _pickup_facilities(session, shipment)
    if facilities > 1:
        raise AppError(
            409,
            "conflict",
            MULTI_FACILITY,
            {"reason": "multiple_pickup_facilities", "facility_count": facilities},
        )
    driver: Driver = await _own(session, user, Driver, driver_id)
    vehicle: Vehicle = await _own(session, user, Vehicle, vehicle_id)
    if not driver.active:
        raise AppError(400, "validation", "This driver is not active.", {"driver_id": driver_id})
    refusal = cold_chain_vehicle_refusal(
        shipment.requires_cold_chain, vehicle.has_cold_chain, vehicle.reg_no
    )
    if refusal:
        raise AppError(
            400,
            "validation",
            refusal,
            {"reason": "cold_chain_vehicle_required", "vehicle_id": vehicle.id},
        )

    pickup, drop = await _ensure_legs(session, shipment)
    route = await routing.ROUTING.route(Point(pickup.lat, pickup.lng), Point(drop.lat, drop.lng))
    if planned is None:
        pickup.planned_at = now
        eta = now + timedelta(hours=transport_eta_hours(route.distance_km))
    else:
        pickup.planned_at, eta = planned
    drop.planned_at = eta
    removed = await _take_off_device(session, shipment)
    shipment.driver_id, shipment.vehicle_id = driver.id, vehicle.id
    shipment.carrier_org_id = user.org_id
    shipment.eta = eta
    shipment.route_distance_km = round(route.distance_km, 3)
    shipment.route_provider = route.provider
    shipment.route_geometry = geojson_line(route.path)
    changes: dict[str, Any] = {
        "driver_id": driver.id,
        "vehicle_id": vehicle.id,
        "carrier_org_id": user.org_id,
        "eta": eta,
        "route_distance_km": shipment.route_distance_km,
        "route_provider": route.provider,
        **({"planned_pickup_at": planned[0]} if planned else {}),
    }
    if removed:
        changes["device_id"] = None
    await _move(session, shipment, S.ASSIGNED, user, reason, now, changes=changes)
    return shipment


async def unassign(
    session: AsyncSession,
    user: User,
    shipment_id: uuid.UUID,
    *,
    reason: str | None,
    now: datetime | None = None,
) -> Shipment:
    """ASSIGNED -> CREATED, by the carrier org only (403 otherwise): the driver, vehicle,
    carrier, any device and the route computed at assignment are cleared, and the shipment
    is back on every logistics org's dispatch board."""
    now = now or datetime.now(UTC)
    shipment = await get_visible(session, user, shipment_id, lock=True)
    if shipment.carrier_org_id is not None and shipment.carrier_org_id != user.org_id:
        raise AppError(403, "forbidden", "Another organization carries this shipment.")
    _check(shipment, S.CREATED, shipment.status == S.ASSIGNED)
    left = shipment.carrier_org_id
    for leg in await legs_of(session, shipment.id):  # before the changes below: autoflush
        leg.planned_at = shipment.planned_eta if leg.stop_type == StopType.DROP else None
    removed = await _take_off_device(session, shipment)
    shipment.driver_id = shipment.vehicle_id = shipment.carrier_org_id = None
    shipment.eta = shipment.route_distance_km = None
    shipment.route_provider = None
    shipment.route_geometry = None
    changes: dict[str, Any] = {"driver_id": None, "vehicle_id": None, "carrier_org_id": None}
    if removed:
        changes["device_id"] = None
    await _move(
        session, shipment, S.CREATED, user, reason, now, changes=changes, notify_also=[left]
    )
    return shipment


async def driver_of(session: AsyncSession, user: User) -> Driver | None:
    return await session.scalar(select(Driver).where(Driver.user_id == user.id))


async def _assigned_driver(session: AsyncSession, user: User, shipment: Shipment) -> Driver:
    driver = await driver_of(session, user)
    if driver is None or shipment.driver_id is None or driver.id != shipment.driver_id:
        raise AppError(403, "forbidden", "Only the shipment's assigned driver can do this.")
    return driver


async def move(
    session: AsyncSession,
    user: User,
    shipment_id: uuid.UUID,
    to: ShipmentStatus,
    *,
    reason: str | None,
    now: datetime | None = None,
) -> Shipment:
    """The assigned driver moves the shipment one step: ASSIGNED -> PICKED_UP -> IN_TRANSIT
    -> DELIVERED. 403 unless the caller's org is involved, 409 for any other step (a
    skipped state included), then 403 unless the caller is the assigned driver.
    At PICKED_UP the source's stock and FIRM hold are drawn down (§9)."""
    now = now or datetime.now(UTC)
    shipment = await get_visible(session, user, shipment_id, lock=True)
    _check(shipment, to, driver_may_move(shipment.status, to))
    await _assigned_driver(session, user, shipment)
    changes: dict[str, Any] = {}
    if to == S.PICKED_UP:
        consumed = await _draw_down(session, shipment, now)
        if consumed:
            changes["consumed_hold_ids"] = consumed
    if to in (S.PICKED_UP, S.DELIVERED):
        kind = StopType.PICKUP if to == S.PICKED_UP else StopType.DROP
        for leg in await legs_of(session, shipment.id):
            if leg.stop_type == kind:
                leg.actual_at = now
    await _move(session, shipment, to, user, reason, now, changes=changes)
    return shipment


async def _draw_down(session: AsyncSession, shipment: Shipment, now: datetime) -> list[str]:
    """§9: at pickup the source's on_hand drops by each FIRM hold's qty and the hold is
    CONSUMED. Rows go to the source org as SYSTEM (the driver is another org's user);
    `inventory.changed` goes to the source org. A purchase order has no holds (supplier
    stock is not tracked as batches). A short pickup (§8/§9: the batch now records less on
    hand than its hold) draws down only what the batch records, never below 0, still
    consumes the hold, and the row states both figures; the shortfall surfaces at receipt
    and reconciliation opens the residual."""
    if shipment.source_request_id is None:
        return []
    rows: Sequence[tuple[Hold, InventoryBatch]] = (
        await session.execute(
            select(Hold, InventoryBatch)
            .join(InventoryBatch, InventoryBatch.id == Hold.batch_id)
            .where(
                Hold.source_request_id == shipment.source_request_id,
                Hold.status == HoldStatus.FIRM,
            )
            .order_by(InventoryBatch.id, Hold.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    if not rows:
        raise AppError(
            409,
            "conflict",
            "The source holds no firm stock for this shipment.",
            {"source_request_id": shipment.source_request_id},
        )
    source = shipment.from_org_id
    for hold, batch in rows:
        before = batch.on_hand
        drawn, cause = short_pickup(before, hold.qty)
        batch.on_hand = before - drawn
        await session.flush()
        await audit.record(
            session,
            None,
            BATCH,
            batch.id,
            f"{BATCH}.picked_up",
            {"on_hand": before},
            {
                "on_hand": batch.on_hand,
                "shipment_id": shipment.id,
                "hold_id": hold.id,
                "held_qty": hold.qty,
                "drawn_qty": drawn,
            },
            cause,
            org_id=source,
        )
        await holds.move_hold(session, hold, source, HoldStatus.CONSUMED, None, cause)
    batches = {b.id: b for _, b in rows}
    await events.emit(
        session,
        EventType.INVENTORY_CHANGED,
        [source],
        {
            "batch_ids": list(batches),
            "product_ids": sorted({b.product_id for b in batches.values()}, key=str),
        },
    )
    return [str(h.id) for h, _ in rows]


async def mark_reconciled(
    session: AsyncSession,
    shipment: Shipment,
    actor: User,
    reason: str | None,
    now: datetime,
    *,
    changes: dict[str, Any],
) -> None:
    """DELIVERED -> RECONCILED when the receiving org records the receipt (S12, §9); 409
    from any other state. The caller has checked that `actor` belongs to the receiving org
    and holds the shipment's lock."""
    await _move(session, shipment, S.RECONCILED, actor, reason, now, changes=changes)


async def has_open_excursion(session: AsyncSession, shipment: Shipment) -> bool:
    """Whether the shipment has a cold-chain excursion on record that is still open
    (business-rules.md §9, §11): its receipt then needs an inspection note. Always False
    until S15 records cold-chain events."""
    return False


# --- location pings ---------------------------------------------------------------------------


async def ping(
    session: AsyncSession,
    user: User,
    shipment_id: uuid.UUID,
    *,
    lat: float,
    lng: float,
    ts: datetime | None,
    now: datetime | None = None,
) -> LocationPing:
    """The assigned driver's phone position, stored and sent as `shipment.location` to the
    shipment's orgs. Only while ASSIGNED, PICKED_UP or IN_TRANSIT (409 otherwise). A ping is
    not a state change, so it writes no audit row."""
    now = now or datetime.now(UTC)
    shipment = await get_visible(session, user, shipment_id)
    await _assigned_driver(session, user, shipment)
    if shipment.status not in ACTIVE_SHIPMENT:
        raise AppError(
            409,
            "conflict",
            f"The shipment is {shipment.status}; locations are taken only on the way.",
            {"status": shipment.status},
        )
    at = ts or now
    if at > now + timedelta(minutes=5):
        raise AppError(400, "validation", "The ping's time is in the future.", {"ts": at})
    location = LocationPing(shipment_id=shipment.id, lat=lat, lng=lng, ts=at)
    session.add(location)
    await session.flush()
    await events.emit(
        session,
        EventType.SHIPMENT_LOCATION,
        await audience(session, shipment),
        {"shipment_id": shipment.id, "lat": lat, "lng": lng, "ts": at},
    )
    return location


async def _assigned_since(session: AsyncSession, shipment: Shipment) -> datetime | None:
    """When the current assignment was recorded, by the database clock: its audit row's
    `ts` (both it and a ping's `created_at` are `clock_timestamp()`, so a phone's `ts`,
    which may run up to 5 min ahead, cannot place an old ping after it). The row is in the
    carrier's org, the org that assigned it."""
    return await session.scalar(
        select(func.max(AuditLog.ts)).where(
            AuditLog.org_id == shipment.carrier_org_id,
            AuditLog.entity == ENTITY,
            AuditLog.entity_id == shipment.id,
            AuditLog.action == f"{ENTITY}.status_changed",
            AuditLog.after["status"].astext == S.ASSIGNED,
        )
    )


async def last_location(session: AsyncSession, shipment: Shipment) -> LocationPing | None:
    """The newest ping recorded since the current assignment; none while the shipment is
    CREATED. An earlier carrier's driver positions never reach the carrier that took the
    shipment over, nor the logistics orgs on the dispatch board (CLAUDE.md rule 6)."""
    if shipment.status == S.CREATED:
        return None
    since = await _assigned_since(session, shipment)
    if since is None:
        return None
    return await session.scalar(
        select(LocationPing)
        .where(LocationPing.shipment_id == shipment.id, LocationPing.created_at > since)
        .order_by(LocationPing.ts.desc(), LocationPing.id.desc())
        .limit(1)
    )
