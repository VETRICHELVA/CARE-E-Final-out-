"""Route plans for one driver (S16): the optimizer over the caller's org's driver and the
unassigned shipments it picks, and applying a plan through S11's assignment, so every
assignment check (same-org driver and vehicle, active driver, cold-chain vehicle, the
state machine) and its audit row and event still apply."""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app import routing
from app.auth.models import User
from app.domain.fulfillment import MULTI_FACILITY, ShipmentStatus, StopType
from app.errors import AppError
from app.routing import optimizer
from app.shipments import service as shipments
from app.shipments.models import Driver, Vehicle
from app.shortages.models import Shortage


@dataclass(frozen=True)
class Planned:
    plan: optimizer.Plan
    driver: Driver
    vehicle: Vehicle | None


async def _orders(
    session: AsyncSession, user: User, shipment_ids: Sequence[uuid.UUID], *, lock: bool
) -> list[optimizer.Order]:
    """The shipments to plan, in the order given (repeats once): 404 unknown, 403 when
    the caller's org may not see one, 409 unless every one is unassigned (CREATED).
    With `lock`, rows are locked in id order so two plans never wait on each other."""
    ids = list(dict.fromkeys(shipment_ids))
    rows = {
        sid: await shipments.get_visible(session, user, sid, lock=lock)
        for sid in sorted(ids, key=str)
    }
    orders = []
    for sid in ids:
        shipment = rows[sid]
        if shipment.status != ShipmentStatus.CREATED:
            raise AppError(
                409,
                "invalid_transition",
                f"Shipment {sid} is {shipment.status}; only unassigned shipments can be planned.",
                {"shipment_id": sid, "status": shipment.status},
            )
        pickup, drop = await shipments.stops_of(session, shipment)
        shortage = await session.get_one(Shortage, shipment.shortage_id)
        several = await shipments.pickup_facilities(session, shipment) > 1
        orders.append(
            optimizer.Order(
                shipment_id=sid,
                pickup_place=pickup.place,
                pickup=pickup.point,
                drop_place=drop.place,
                drop=drop.point,
                required_by=shortage.required_by,
                cold_chain=shipment.requires_cold_chain,
                blocked=MULTI_FACILITY if several else None,
            )
        )
    return orders


async def optimize(
    session: AsyncSession,
    user: User,
    *,
    driver_id: uuid.UUID,
    vehicle_id: uuid.UUID | None,
    shipment_ids: Sequence[uuid.UUID],
    timezone: str,
    now: datetime | None = None,
    lock: bool = False,
) -> Planned:
    """A stop order for the caller's org's driver over the given shipments, starting now.
    Writes nothing. The travel matrix is one table call (OSRM, haversine fallback); the
    search (up to 5 s) runs in a worker thread so the hub keeps serving."""
    now = now or datetime.now(UTC)
    driver = await shipments.own_driver(session, user, driver_id)
    vehicle = await shipments.own_vehicle(session, user, vehicle_id) if vehicle_id else None
    orders = await _orders(session, user, shipment_ids, lock=lock)
    travel = await optimizer.duration_matrix(optimizer.points_of(orders), routing.ROUTING)
    plan = await asyncio.to_thread(
        optimizer.plan,
        orders,
        travel,
        now,
        ZoneInfo(timezone),
        vehicle=(vehicle.has_cold_chain, vehicle.reg_no) if vehicle else None,
    )
    return Planned(plan, driver, vehicle)


async def apply(
    session: AsyncSession,
    user: User,
    *,
    driver_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    shipment_ids: Sequence[uuid.UUID],
    timezone: str,
    reason: str | None,
    now: datetime | None = None,
) -> tuple[Planned, list[uuid.UUID]]:
    """Plan again (the hub decides; the shipments are locked meanwhile) and assign the
    driver and vehicle to every feasible shipment through S11's `assign`, with the legs'
    `planned_at` and the ETA from the plan. All or nothing: if one assignment is refused,
    none is kept. 409 `conflict` when no shipment fits."""
    now = now or datetime.now(UTC)
    planned = await optimize(
        session,
        user,
        driver_id=driver_id,
        vehicle_id=vehicle_id,
        shipment_ids=shipment_ids,
        timezone=timezone,
        now=now,
        lock=True,
    )
    pickups: dict[uuid.UUID, datetime] = {}
    drops: dict[uuid.UUID, datetime] = {}
    for stop in planned.plan.stops:
        (pickups if stop.kind == StopType.PICKUP else drops)[stop.shipment_id] = stop.eta
    if not pickups:
        raise AppError(
            409,
            "conflict",
            "None of these shipments fits a route for this driver; nothing was assigned.",
            {
                "infeasible": [
                    {"shipment_id": i.shipment_id, "reason": i.reason}
                    for i in planned.plan.infeasible
                ]
            },
        )
    async with session.begin_nested():
        for shipment_id, pickup_at in pickups.items():
            await shipments.assign(
                session,
                user,
                shipment_id,
                driver_id=driver_id,
                vehicle_id=vehicle_id,
                reason=reason,
                now=now,
                planned=(pickup_at, drops[shipment_id]),
            )
    return planned, list(pickups)
