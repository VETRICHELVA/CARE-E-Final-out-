from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.capabilities import Capability
from app.auth.deps import require
from app.auth.models import User
from app.db import SessionDep
from app.routing import service
from app.routing.schemas import (
    ApplyIn,
    InfeasibleOut,
    OptimizeIn,
    RouteApplyOut,
    RoutePlanOut,
    RouteStopOut,
)

router = APIRouter(tags=["routes"])

# api-and-events.md (S16).
Dispatcher = Annotated[User, Depends(require(Capability.SHIPMENT_ASSIGN))]


def _out(planned: service.Planned) -> RoutePlanOut:
    return RoutePlanOut(
        driver_id=planned.driver.id,
        vehicle_id=planned.vehicle.id if planned.vehicle else None,
        stops=[
            RouteStopOut(
                seq=n,
                shipment_id=s.shipment_id,
                type=s.kind,
                place=s.place,
                lat=s.point.lat,
                lng=s.point.lng,
                eta=s.eta,
            )
            for n, s in enumerate(planned.plan.stops, 1)
        ],
        infeasible=[
            InfeasibleOut(shipment_id=i.shipment_id, reason=i.reason)
            for i in planned.plan.infeasible
        ],
    )


@router.post("/routes/optimize")
async def optimize_route(body: OptimizeIn, user: Dispatcher, session: SessionDep) -> RoutePlanOut:
    """A stop order for one of the caller's org's drivers over unassigned shipments,
    starting now: each pickup before its drop, each drop by its shortage's `required_by`,
    each cold-chain shipment within the cold-chain ride limit (business-rules.md §4).
    Shipments that cannot fit come back in `infeasible` with a reason. Writes nothing.
    403 for another org's driver, vehicle or a shipment the caller's org cannot see; 409
    unless every shipment is CREATED."""
    planned = await service.optimize(
        session,
        user,
        driver_id=body.driver_id,
        vehicle_id=body.vehicle_id,
        shipment_ids=body.shipment_ids,
        timezone=body.timezone,
    )
    return _out(planned)


@router.post("/routes/apply")
async def apply_route(body: ApplyIn, user: Dispatcher, session: SessionDep) -> RouteApplyOut:
    """Plans again and assigns the driver and vehicle to every feasible shipment (as
    POST /shipments/{id}/assign does, with its checks and audit rows), with each stop's
    `planned_at` and the ETA from the plan. All or nothing; 409 `conflict` if no shipment
    fits, `invalid_transition` unless every shipment is CREATED."""
    planned, assigned = await service.apply(
        session,
        user,
        driver_id=body.driver_id,
        vehicle_id=body.vehicle_id,
        shipment_ids=body.shipment_ids,
        timezone=body.timezone,
        reason=body.reason,
    )
    out = RouteApplyOut(**_out(planned).model_dump(), assigned_shipment_ids=assigned)
    await session.commit()
    return out
