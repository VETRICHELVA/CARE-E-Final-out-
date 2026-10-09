import uuid
from collections.abc import Sequence
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.capabilities import Capability
from app.auth.deps import CurrentUser, org_scoped, require
from app.auth.models import User
from app.catalog.models import Product
from app.coldchain import service as coldchain_service
from app.db import SessionDep
from app.domain.fulfillment import RouteProvider, ShipmentStatus, StopType
from app.orgs.models import Organization
from app.pagination import Cursor, Limit, Page, paginate
from app.receiving import router as receiving
from app.receiving import service as receiving_service
from app.shipments import service
from app.shipments.models import Driver, Shipment, ShipmentLeg, Vehicle
from app.shipments.schemas import (
    AssignIn,
    DriverOut,
    DriverRef,
    LocationIn,
    LocationOut,
    ShipmentDetailOut,
    ShipmentOut,
    StatusChangeOut,
    StatusIn,
    StopOut,
    VehicleOut,
)
from app.shortages.models import Priority, Shortage
from app.shortages.schemas import ReasonIn

router = APIRouter(tags=["shipments"])

# api-and-events.md (S11).
Dispatcher = Annotated[User, Depends(require(Capability.SHIPMENT_ASSIGN))]
DriverUser = Annotated[User, Depends(require(Capability.SHIPMENT_UPDATE_STATUS))]


async def _present(
    session: AsyncSession, user: User, rows: Sequence[Shipment]
) -> list[ShipmentOut]:
    if not rows:
        return []
    coldchain = await coldchain_service.summaries(session, user, rows)
    org_ids = {o for s in rows for o in (s.from_org_id, s.to_org_id, s.carrier_org_id) if o}
    names: dict[uuid.UUID, str] = {
        org_id: name
        for org_id, name in await session.execute(
            select(Organization.id, Organization.name).where(Organization.id.in_(org_ids))
        )
    }
    products = {
        p.id: p
        for p in await session.scalars(
            select(Product).where(Product.id.in_({s.product_id for s in rows}))
        )
    }
    shortages = {
        s.id: s
        for s in await session.scalars(
            select(Shortage).where(Shortage.id.in_({s.shortage_id for s in rows}))
        )
    }
    drivers = {
        d_id: DriverRef(id=d_id, name=name)
        for d_id, name in await session.execute(
            select(Driver.id, User.full_name)
            .join(User, User.id == Driver.user_id)
            .where(Driver.id.in_({s.driver_id for s in rows if s.driver_id}))
        )
    }
    vehicles = {
        v.id: VehicleOut.model_validate(v)
        for v in await session.scalars(
            select(Vehicle).where(Vehicle.id.in_({s.vehicle_id for s in rows if s.vehicle_id}))
        )
    }
    stops: dict[tuple[uuid.UUID, str], StopOut] = {
        (leg.shipment_id, leg.stop_type): StopOut.model_validate(leg)
        for leg in await session.scalars(
            select(ShipmentLeg).where(ShipmentLeg.shipment_id.in_({s.id for s in rows}))
        )
    }
    out = []
    for s in rows:
        product, shortage = products[s.product_id], shortages[s.shortage_id]
        out.append(
            ShipmentOut(
                id=s.id,
                shortage_id=s.shortage_id,
                source_request_id=s.source_request_id,
                purchase_order_id=s.purchase_order_id,
                from_org_id=s.from_org_id,
                from_org_name=names[s.from_org_id],
                to_org_id=s.to_org_id,
                to_org_name=names[s.to_org_id],
                carrier_org_id=s.carrier_org_id,
                carrier_org_name=names.get(s.carrier_org_id) if s.carrier_org_id else None,
                product_id=s.product_id,
                product_code=product.code,
                product_name=product.name,
                qty=s.qty,
                requires_cold_chain=s.requires_cold_chain,
                status=ShipmentStatus(s.status),
                priority=Priority(shortage.priority),
                required_by=shortage.required_by,
                driver=drivers.get(s.driver_id) if s.driver_id else None,
                vehicle=vehicles.get(s.vehicle_id) if s.vehicle_id else None,
                device_id=s.device_id,
                planned_eta=s.planned_eta,
                eta=s.eta,
                route_distance_km=s.route_distance_km,
                route_provider=RouteProvider(s.route_provider) if s.route_provider else None,
                pickup=stops.get((s.id, StopType.PICKUP)),
                drop=stops.get((s.id, StopType.DROP)),
                coldchain=coldchain.get(s.id),
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
        )
    return out


async def _detail(session: AsyncSession, user: User, shipment: Shipment) -> ShipmentDetailOut:
    await session.flush()
    await session.refresh(shipment)  # updated_at is set by the database
    (base,) = await _present(session, user, [shipment])
    last = await service.last_location(session, shipment)
    receipt = None
    if user.org_id == shipment.to_org_id:  # the receiver's record (CLAUDE.md rule 6)
        row = await receiving_service.receipt_for(session, shipment.id)
        receipt = await receiving.receipt_out(session, row) if row else None
    return ShipmentDetailOut(
        **base.model_dump(),
        route_geometry=shipment.route_geometry,
        status_history=[
            StatusChangeOut(from_status=h["from"], to_status=h["to"], at=h["at"])
            for h in shipment.status_history or []
        ],
        last_location=LocationOut.model_validate(last) if last else None,
        inspection_note_required=await service.has_open_excursion(session, shipment),
        receipt=receipt,
    )


@router.get("/shipments")
async def list_shipments(
    user: CurrentUser,
    session: SessionDep,
    status: ShipmentStatus | None = None,
    assigned_to_me: Annotated[
        bool, Query(description="Only the shipments assigned to the caller as driver.")
    ] = False,
    direction: Annotated[
        Literal["inbound", "outbound"] | None,
        Query(
            description="inbound: shipments to the caller's org (its deliveries); "
            "outbound: shipments from it."
        ),
    ] = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Page[ShipmentOut]:
    """Shipments the caller's org is involved in (from, to or carrier), newest first; a
    LOGISTICS org also sees every unassigned (CREATED) shipment: the dispatch board."""
    stmt = service.visible_to(select(Shipment), user)
    if status is not None:
        stmt = stmt.where(Shipment.status == status)
    if direction == "inbound":
        stmt = stmt.where(Shipment.to_org_id == user.org_id)
    elif direction == "outbound":
        stmt = stmt.where(Shipment.from_org_id == user.org_id)
    if assigned_to_me:
        driver = await service.driver_of(session, user)
        if driver is None:  # never `driver_id IS NULL`: that would list unassigned shipments
            return Page[ShipmentOut](items=[], next_cursor=None)
        stmt = stmt.where(Shipment.driver_id == driver.id)
    rows, next_cursor = await paginate(
        session, stmt, Shipment.created_at, Shipment.id, limit, cursor, newest_first=True
    )
    return Page[ShipmentOut](items=await _present(session, user, rows), next_cursor=next_cursor)


@router.get("/shipments/{shipment_id}")
async def get_shipment(
    shipment_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> ShipmentDetailOut:
    """One shipment with its route geometry, status history and last driver location (only
    a ping recorded since the current assignment; none while CREATED). 403 unless the
    caller's org is involved (or it is unassigned and the caller is a logistics org)."""
    return await _detail(session, user, await service.get_visible(session, user, shipment_id))


@router.post("/shipments/{shipment_id}/assign")
async def assign_shipment(
    shipment_id: uuid.UUID, body: AssignIn, user: Dispatcher, session: SessionDep
) -> ShipmentDetailOut:
    """CREATED -> ASSIGNED with one of the caller's org's drivers and vehicles (403 for
    another org's). A cold-chain shipment needs a cold-chain vehicle: 400 with the reason
    otherwise. Stores the road route, its geometry and the ETA (OSRM, haversine fallback).
    409 unless CREATED, and 409 `conflict` when the held stock is at more than one of the
    source's facilities (one pickup per shipment). Any device on it comes off."""
    shipment = await service.assign(
        session,
        user,
        shipment_id,
        driver_id=body.driver_id,
        vehicle_id=body.vehicle_id,
        reason=body.reason,
    )
    out = await _detail(session, user, shipment)
    await session.commit()
    return out


@router.post("/shipments/{shipment_id}/unassign")
async def unassign_shipment(
    shipment_id: uuid.UUID, user: Dispatcher, session: SessionDep, body: ReasonIn | None = None
) -> ShipmentDetailOut:
    """ASSIGNED -> CREATED, by the carrier org (403 otherwise); 409 unless ASSIGNED. Any
    device on it comes off."""
    shipment = await service.unassign(
        session, user, shipment_id, reason=body.reason if body else None
    )
    out = await _detail(session, user, shipment)
    await session.commit()
    return out


@router.post("/shipments/{shipment_id}/status")
async def update_shipment_status(
    shipment_id: uuid.UUID, body: StatusIn, user: DriverUser, session: SessionDep
) -> ShipmentDetailOut:
    """The assigned driver only (403 otherwise): ASSIGNED -> PICKED_UP -> IN_TRANSIT ->
    DELIVERED, one step at a time (409 otherwise). PICKED_UP draws down the source's
    on_hand and consumes its FIRM hold; a batch recording less than its hold is drawn down
    only by what it records (never below 0)."""
    shipment = await service.move(session, user, shipment_id, body.status, reason=body.reason)
    out = await _detail(session, user, shipment)
    await session.commit()
    return out


@router.post("/shipments/{shipment_id}/location")
async def post_shipment_location(
    shipment_id: uuid.UUID, body: LocationIn, user: DriverUser, session: SessionDep
) -> LocationOut:
    """A GPS ping from the assigned driver's phone (403 otherwise), while ASSIGNED,
    PICKED_UP or IN_TRANSIT (409 otherwise). Sent to the shipment's orgs as
    `shipment.location`."""
    location = await service.ping(
        session, user, shipment_id, lat=body.lat, lng=body.lng, ts=body.ts
    )
    out = LocationOut.model_validate(location)
    await session.commit()
    return out


@router.get("/drivers")
async def list_drivers(
    user: Dispatcher, session: SessionDep, limit: Limit = 50, cursor: Cursor = None
) -> Page[DriverOut]:
    """The caller's own org's drivers."""
    rows, next_cursor = await paginate(
        session, org_scoped(select(Driver), user), Driver.created_at, Driver.id, limit, cursor
    )
    names: dict[uuid.UUID, str] = {
        user_id: name
        for user_id, name in await session.execute(
            select(User.id, User.full_name).where(User.id.in_({d.user_id for d in rows}))
        )
    }
    return Page[DriverOut](
        items=[
            DriverOut(
                id=d.id, user_id=d.user_id, name=names[d.user_id], phone=d.phone, active=d.active
            )
            for d in rows
        ],
        next_cursor=next_cursor,
    )


@router.get("/vehicles")
async def list_vehicles(
    user: Dispatcher, session: SessionDep, limit: Limit = 50, cursor: Cursor = None
) -> Page[VehicleOut]:
    """The caller's own org's vehicles."""
    rows, next_cursor = await paginate(
        session, org_scoped(select(Vehicle), user), Vehicle.created_at, Vehicle.id, limit, cursor
    )
    return Page[VehicleOut](
        items=[VehicleOut.model_validate(v) for v in rows], next_cursor=next_cursor
    )
