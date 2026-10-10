"""Route optimization for one driver (S16): the order of pickups and drops that serves the
most shipments, each pickup before its drop, every drop by its shortage's `required_by`,
and every cold-chain shipment within COLD_CHAIN_MAX_TRANSIT (business-rules.md §4).

- `duration_matrix`: travel seconds between every pair of stops, from the routing
  provider's table (OSRM `/table`, haversine x ROAD_FACTOR when OSRM is off or failing),
  turned into time at AVG_SPEED_KMH as §4 does for every transport ETA.
- `solve`: a pickup-and-delivery problem with time windows on Google OR-Tools, searched
  for at most SEARCH_LIMIT (5 s). The route starts at its first pickup now (drivers have no
  depot or position on record) and ends at its last drop. HANDOVER_HOURS (§4's handover
  allowance) is spent at each pickup, so a lone shipment arrives at now + distance ÷ 40
  km/h + 1 h, the ETA S11's assignment stores. Shipments that cannot fit are left out.
- `plan`: checks each shipment on its own first (the vehicle, a passed deadline, the
  direct ride against the cold-chain limit and the deadline), solves for the rest, and
  gives every shipment left out a plain-language reason. Nothing is dropped silently.

Everything here is pure (no database); the service runs `plan` in a worker thread."""

import math
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.domain import config
from app.domain.costing import Point, RoutingProvider
from app.domain.fulfillment import StopType, cold_chain_vehicle_refusal

SEARCH_LIMIT = timedelta(seconds=5)
# Leaving a shipment out costs more than any route can, so the solver serves as many
# shipments as fit and only then minimises the driving time.
_DROP_PENALTY = 10**12


def travel_seconds(distance_km: float) -> int:
    """§4: road distance at AVG_SPEED_KMH."""
    return round(distance_km / config.AVG_SPEED_KMH * 3600)


async def duration_matrix(points: Sequence[Point], provider: RoutingProvider) -> list[list[int]]:
    """Travel seconds from every point (rows) to every point (columns): one table call to
    the provider (OSRM's table service, haversine when OSRM is off, failing or slow)."""
    if not points:
        return []
    km = await provider.table(points, points)
    return [[travel_seconds(d) for d in row] for row in km]


# --- the solver ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Job:
    """One shipment for the solver: its pickup and drop as node indices into the travel
    matrix, the latest arrival at the drop and, for cold chain, the longest allowed time
    from arriving at the pickup to arriving at the drop (seconds from now)."""

    pickup: int
    drop: int
    deadline: int
    max_ride: int | None = None


@dataclass(frozen=True)
class Visit:
    job: int  # index into the jobs
    kind: StopType
    at: int  # arrival, seconds from now


@dataclass(frozen=True)
class Solution:
    visits: list[Visit]  # in driving order
    left_out: list[int]  # job indices that could not be served


GUIDED_HEADROOM = timedelta(milliseconds=500)


def search_parameters(time_limit: timedelta = SEARCH_LIMIT, *, guided: bool = False) -> Any:
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    # Greedy descent stops at a local optimum (milliseconds for a driver's day); guided
    # local search keeps improving until the time limit.
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        if guided
        else routing_enums_pb2.LocalSearchMetaheuristic.AUTOMATIC
    )
    params.time_limit.FromTimedelta(time_limit)
    return params


def solve(
    travel: Sequence[Sequence[int]],
    service: Sequence[int],
    jobs: Sequence[Job],
    *,
    time_limit: timedelta = SEARCH_LIMIT,
    guided: bool = False,
) -> Solution:
    """One vehicle, open start and end. `travel[i][j]` is seconds from node i to node j of
    the matrix and `service[i]` the seconds spent at node i before leaving it. A job whose
    deadline has already passed is left out without searching."""
    possible = [k for k, job in enumerate(jobs) if job.deadline >= 0]
    left_out = [k for k, job in enumerate(jobs) if job.deadline < 0]
    if not possible:
        return Solution([], left_out)

    # Model nodes: 0 is a virtual start and end, zero time from and to every stop; each
    # possible job then has its own pickup and drop node, so jobs left out before the
    # search are not in the model at all.
    stops: list[tuple[int, StopType]] = []  # model node - 1 -> (job, kind)
    where: list[int] = []  # model node - 1 -> node of the travel matrix
    for k in possible:
        stops += [(k, StopType.PICKUP), (k, StopType.DROP)]
        where += [jobs[k].pickup, jobs[k].drop]

    def seconds(a: int, b: int) -> int:
        if a == 0 or b == 0 or a == b:
            return 0
        i, j = where[a - 1], where[b - 1]
        return int(service[i]) + int(travel[i][j])

    size = len(stops) + 1
    manager = pywrapcp.RoutingIndexManager(size, 1, 0)
    model = pywrapcp.RoutingModel(manager)
    transit = model.RegisterTransitMatrix(
        [[seconds(a, b) for b in range(size)] for a in range(size)]
    )
    model.SetArcCostEvaluatorOfAllVehicles(transit)
    horizon = max(jobs[k].deadline for k in possible)
    # No waiting (slack 0): every window opens now, so waiting never helps.
    model.AddDimension(transit, 0, horizon, True, "time")
    time = model.GetDimensionOrDie("time")
    solver = model.solver()
    for n, k in enumerate(possible):
        job = jobs[k]
        p, d = manager.NodeToIndex(2 * n + 1), manager.NodeToIndex(2 * n + 2)
        model.AddPickupAndDelivery(p, d)
        solver.Add(model.VehicleVar(p) == model.VehicleVar(d))
        solver.Add(model.ActiveVar(p) == model.ActiveVar(d))
        solver.Add(time.CumulVar(p) <= time.CumulVar(d))
        time.CumulVar(p).SetMax(job.deadline)
        time.CumulVar(d).SetMax(job.deadline)
        if job.max_ride is not None:
            solver.Add(time.CumulVar(d) - time.CumulVar(p) <= job.max_ride)
        model.AddDisjunction([p], _DROP_PENALTY)
        model.AddDisjunction([d], _DROP_PENALTY)

    found = model.SolveWithParameters(search_parameters(time_limit, guided=guided))
    if found is None:
        return Solution([], sorted(left_out + possible))
    visits = []
    index = found.Value(model.NextVar(model.Start(0)))
    while not model.IsEnd(index):
        k, kind = stops[manager.IndexToNode(index) - 1]
        visits.append(Visit(k, kind, found.Value(time.CumulVar(index))))
        index = found.Value(model.NextVar(index))
    served = {v.job for v in visits}
    return Solution(visits, sorted(left_out + [k for k in possible if k not in served]))


# --- the plan, with reasons ---------------------------------------------------------------------


@dataclass(frozen=True)
class Order:
    """A shipment to plan: where it is picked up and dropped, its shortage's deadline,
    and whether it needs the cold chain."""

    shipment_id: uuid.UUID
    pickup_place: str
    pickup: Point
    drop_place: str
    drop: Point
    required_by: datetime
    cold_chain: bool
    blocked: str | None = None  # a reason it cannot be planned at all (e.g. several pickups)


@dataclass(frozen=True)
class PlannedStop:
    shipment_id: uuid.UUID
    kind: StopType
    place: str
    point: Point
    eta: datetime


@dataclass(frozen=True)
class Infeasible:
    shipment_id: uuid.UUID
    reason: str


@dataclass(frozen=True)
class Plan:
    stops: list[PlannedStop]
    infeasible: list[Infeasible]


def points_of(orders: Sequence[Order]) -> list[Point]:
    """The matrix nodes for `plan`: order k's pickup is node 2k and its drop node 2k + 1."""
    return [p for o in orders for p in (o.pickup, o.drop)]


def clock(at: datetime, now: datetime, tz: tzinfo, *, up: bool = False) -> str:
    """'14:00 IST' today, '9 Oct 14:00 IST' on another day, in the planner's time zone.
    `up` rounds a part minute up (an earliest arrival at 14:00:40 is "14:01", never a
    time at or before a 14:00 deadline it misses)."""
    if up and (at.second or at.microsecond):
        at = at.replace(second=0, microsecond=0) + timedelta(minutes=1)
    local = at.astimezone(tz)
    day = "" if local.date() == now.astimezone(tz).date() else f"{local.day} {local:%b} "
    return f"{day}{local:%H:%M} {local.tzname()}".rstrip()


def span(seconds: float) -> str:
    """'3 h 25 min', '4 h', '50 min'."""
    h, m = divmod(math.ceil(seconds / 60), 60)
    if h and m:
        return f"{h} h {m} min"
    return f"{h} h" if h else f"{m} min"


def plan(
    orders: Sequence[Order],
    travel: Sequence[Sequence[int]],
    now: datetime,
    tz: tzinfo,
    *,
    vehicle: tuple[bool, str] | None = None,
    time_limit: timedelta = SEARCH_LIMIT,
) -> Plan:
    """Plan `orders` for one driver. `travel` is the matrix over `points_of(orders)`;
    `vehicle` is (has_cold_chain, reg_no) when a vehicle was chosen. Infeasible shipments
    come back in the order given, each with its reason."""
    handover = round(config.HANDOVER_HOURS * 3600)
    max_ride = config.COLD_CHAIN_MAX_TRANSIT.total_seconds()
    limit = span(max_ride)
    service = [handover if node % 2 == 0 else 0 for node in range(2 * len(orders))]

    reasons: dict[int, str] = {}
    jobs: list[Job] = []
    candidates: list[int] = []
    for k, o in enumerate(orders):
        deadline = (o.required_by - now).total_seconds()
        direct = handover + travel[2 * k][2 * k + 1]  # arriving at the pickup now
        by = clock(o.required_by, now, tz)
        refusal = (
            cold_chain_vehicle_refusal(o.cold_chain, vehicle[0], vehicle[1]) if vehicle else None
        )
        if o.blocked:
            reasons[k] = o.blocked
        elif refusal:
            reasons[k] = refusal
        elif deadline < 0:
            reasons[k] = f"Cannot reach {o.drop_place} before {by}: that time has passed."
        elif o.cold_chain and direct > max_ride:
            reasons[k] = (
                f"The ride from {o.pickup_place} to {o.drop_place} takes {span(direct)} with "
                f"the handover, over the {limit} cold-chain limit."
            )
        elif direct > deadline:
            earliest = clock(now + timedelta(seconds=direct), now, tz, up=True)
            reasons[k] = (
                f"Cannot reach {o.drop_place} before {by}: even going straight there, "
                f"the earliest arrival is {earliest}."
            )
        else:
            candidates.append(k)
            jobs.append(
                Job(2 * k, 2 * k + 1, math.floor(deadline), int(max_ride) if o.cold_chain else None)
            )

    # A fast first pass (greedy descent); only if it leaves a shipment out, guided local
    # search over the full time limit, since greedy descent can stop at a local optimum
    # that misses a route serving everything.
    started = time.monotonic()
    solution = solve(travel, service, jobs, time_limit=time_limit)
    # The guided pass gets what is left of the limit, less headroom for OR-Tools' own
    # overrun, so the whole search stays within the brief's 5 s.
    left = time_limit - timedelta(seconds=time.monotonic() - started) - GUIDED_HEADROOM
    if solution.left_out and left > timedelta(milliseconds=100):
        guided = solve(travel, service, jobs, time_limit=left, guided=True)
        if len(guided.left_out) < len(solution.left_out):
            solution = guided
    searched = f"{time_limit.total_seconds():g} s"
    for j in solution.left_out:
        k = candidates[j]
        o = orders[k]
        by = clock(o.required_by, now, tz)
        # Each shipment fits on its own (checked above); the search did not prove the
        # combination impossible, so the reason says only what it found.
        reasons[k] = (
            f"The route search ({searched}) found no order that also reaches "
            f"{o.drop_place} by {by}"
            + (f" within the {limit} cold-chain ride limit" if o.cold_chain else "")
            + " with the other shipments in this route; plan it separately."
        )
    stops = []
    for visit in solution.visits:
        o = orders[candidates[visit.job]]
        pickup = visit.kind == StopType.PICKUP
        stops.append(
            PlannedStop(
                o.shipment_id,
                visit.kind,
                o.pickup_place if pickup else o.drop_place,
                o.pickup if pickup else o.drop,
                now + timedelta(seconds=visit.at),
            )
        )
    return Plan(
        stops,
        [Infeasible(orders[k].shipment_id, reasons[k]) for k in sorted(reasons)],
    )
