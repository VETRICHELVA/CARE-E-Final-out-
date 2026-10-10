"""The S16 optimizer on fixed travel matrices (no database, no OSRM): a feasible plan meets
every window with each pickup before its drop, a deadline that cannot be met and a
cold-chain ride over COLD_CHAIN_MAX_TRANSIT are reported with reasons, ten shipments solve
in under the 5 s search limit, and the limit bounds a search that would run longer."""

import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain import config
from app.domain.costing import HaversineProvider, Point, haversine_km
from app.domain.fulfillment import StopType
from app.routing import optimizer
from app.routing.optimizer import Job, Order, Plan, plan, solve

NOW = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)  # 11:30 IST
IST = ZoneInfo("Asia/Kolkata")
HOUR, MIN = 3600, 60
HANDOVER = config.HANDOVER_HOURS * HOUR


def order(n: int, *, deadline_h: float, cold: bool = False, drop: str | None = None) -> Order:
    return Order(
        shipment_id=uuid.UUID(int=n),
        pickup_place=f"Source {n}",
        pickup=Point(12.9 + n / 100, 77.5),
        drop_place=drop or f"Hospital {n}",
        drop=Point(13.0 + n / 100, 77.6),
        required_by=NOW + timedelta(hours=deadline_h),
        cold_chain=cold,
    )


def matrix(minutes: Sequence[Sequence[float]]) -> list[list[int]]:
    return [[round(m * MIN) for m in row] for row in minutes]


def uniform(nodes: int, minutes: float) -> list[list[int]]:
    return [[0 if i == j else round(minutes * MIN) for j in range(nodes)] for i in range(nodes)]


def check_windows(result: Plan, orders: Sequence[Order]) -> None:
    """Each served shipment: pickup before drop, drop by its deadline, ETAs in order."""
    by_id = {o.shipment_id: o for o in orders}
    seen: dict[uuid.UUID, datetime] = {}
    etas = [s.eta for s in result.stops]
    assert etas == sorted(etas)
    for stop in result.stops:
        if stop.kind == StopType.PICKUP:
            assert stop.shipment_id not in seen
            seen[stop.shipment_id] = stop.eta
        else:
            assert stop.shipment_id in seen, "drop before its pickup"
            assert stop.eta <= by_id[stop.shipment_id].required_by
            if by_id[stop.shipment_id].cold_chain:
                assert stop.eta - seen[stop.shipment_id] <= config.COLD_CHAIN_MAX_TRANSIT
    assert sum(s.kind == StopType.DROP for s in result.stops) == len(seen)


# --- acceptance: 3 shipments, one driver ------------------------------------------------------


def test_three_shipments_for_one_driver_meet_every_window_with_each_pickup_before_its_drop() -> (
    None
):
    orders = [order(1, deadline_h=3), order(2, deadline_h=5, cold=True), order(3, deadline_h=8)]
    # Nodes: P1 D1 P2 D2 P3 D3, minutes of driving between them.
    travel = matrix(
        [
            [0, 30, 10, 40, 50, 60],
            [30, 0, 25, 15, 45, 50],
            [10, 25, 0, 30, 40, 55],
            [40, 15, 30, 0, 20, 35],
            [50, 45, 40, 20, 0, 30],
            [60, 50, 55, 35, 30, 0],
        ]
    )
    result = plan(orders, travel, NOW, IST)
    assert result.infeasible == []
    assert len(result.stops) == 6
    check_windows(result, orders)
    first = result.stops[0]
    assert (first.kind, first.eta) == (StopType.PICKUP, NOW)  # the route starts now
    assert first.place.startswith("Source") and first.point in {o.pickup for o in orders}


def test_a_lone_shipment_arrives_at_the_s11_eta_now_plus_the_ride_and_the_handover() -> None:
    (o,) = orders = [order(1, deadline_h=6)]
    result = plan(orders, matrix([[0, 90], [90, 0]]), NOW, IST)
    pickup, drop = result.stops
    assert (pickup.kind, pickup.eta, pickup.place) == (StopType.PICKUP, NOW, "Source 1")
    assert (drop.kind, drop.place, drop.point) == (StopType.DROP, "Hospital 1", o.drop)
    assert drop.eta == NOW + timedelta(minutes=90) + timedelta(hours=config.HANDOVER_HOURS)


# --- acceptance: a deadline that cannot be met --------------------------------------------------


def test_a_shipment_that_cannot_reach_its_hospital_in_time_is_infeasible_with_a_reason() -> None:
    orders = [
        order(1, deadline_h=6),
        order(2, deadline_h=2.5, drop="Hospital C"),  # 2 h drive + 1 h handover > 2.5 h
    ]
    travel = uniform(4, 30)
    travel[2][3] = travel[3][2] = 120 * MIN
    result = plan(orders, travel, NOW, IST)
    (bad,) = result.infeasible
    assert bad.shipment_id == orders[1].shipment_id
    # 06:00 UTC + 2.5 h = 08:30 UTC = 14:00 IST; earliest 06:00 + 3 h = 09:00 UTC = 14:30 IST.
    assert bad.reason == (
        "Cannot reach Hospital C before 14:00 IST: even going straight there, "
        "the earliest arrival is 14:30 IST."
    )
    assert {s.shipment_id for s in result.stops} == {orders[0].shipment_id}


def test_a_deadline_that_has_passed_is_infeasible() -> None:
    result = plan([order(1, deadline_h=-1, drop="Hospital C")], uniform(2, 10), NOW, UTC)
    (bad,) = result.infeasible
    assert bad.reason == "Cannot reach Hospital C before 05:00 UTC: that time has passed."
    assert result.stops == []


def test_shipments_that_fit_alone_but_not_together_leave_one_out_with_a_reason() -> None:
    # Two shipments, each 1 h 30 min alone (30 min drive + 1 h handover), both due in 2 h,
    # with 2 h between the two areas: only one can be served.
    orders = [order(1, deadline_h=2), order(2, deadline_h=2, drop="Hospital C")]
    travel = matrix([[0, 30, 120, 120], [30, 0, 120, 120], [120, 120, 0, 30], [120, 120, 30, 0]])
    result = plan(orders, travel, NOW, IST)
    (bad,) = result.infeasible
    served = {s.shipment_id for s in result.stops}
    assert len(served) == 1 and bad.shipment_id not in served
    dropped = next(o for o in orders if o.shipment_id == bad.shipment_id)
    assert bad.reason == (
        "The route search (5 s) found no order that also reaches "
        f"{dropped.drop_place} by 13:30 IST with the other shipments in this route; "
        "plan it separately."
    )
    check_windows(result, orders)


def test_a_deadline_on_another_day_names_the_day() -> None:
    o = order(1, deadline_h=30, drop="Hospital C")
    result = plan([o], matrix([[0, 40 * 60], [40 * 60, 0]]), NOW, IST)
    (bad,) = result.infeasible
    assert bad.reason.startswith("Cannot reach Hospital C before 9 Oct 17:30 IST: ")


# --- acceptance: the cold-chain ride limit ------------------------------------------------------


def test_a_cold_chain_ride_over_the_limit_is_infeasible() -> None:
    assert timedelta(hours=4) == config.COLD_CHAIN_MAX_TRANSIT
    # 3 h 10 min drive + 1 h handover = 4 h 10 min > 4 h, though the deadline is far.
    cold = order(1, deadline_h=12, cold=True, drop="Hospital C")
    plain = order(2, deadline_h=12)  # the same ride is fine without the cold chain
    travel = uniform(4, 20)
    travel[0][1] = travel[2][3] = 190 * MIN
    result = plan([cold, plain], travel, NOW, IST)
    (bad,) = result.infeasible
    assert bad.shipment_id == cold.shipment_id
    assert bad.reason == (
        "The ride from Source 1 to Hospital C takes 4 h 10 min with the handover, "
        "over the 4 h cold-chain limit."
    )
    assert {s.shipment_id for s in result.stops} == {plain.shipment_id}


# P1 and P2 are at one place, each drop 30 min from it, the drops 2 h 40 min apart, and
# going back from a drop to the pickups takes 3 h 20 min. Picking both up first is the
# shortest drive, but holds the second cold shipment 4 h 10 min.
PAIR = matrix([[0, 30, 0, 30], [200, 0, 200, 160], [0, 30, 0, 30], [200, 160, 200, 0]])


def test_a_cold_chain_pair_is_kept_within_the_limit_by_the_stop_order() -> None:
    orders = [order(1, deadline_h=12, cold=True), order(2, deadline_h=12, cold=True)]
    result = plan(orders, PAIR, NOW, IST)
    assert result.infeasible == []
    check_windows(result, orders)
    kinds = [(s.shipment_id, s.kind) for s in result.stops]
    # Not the shortest drive (both pickups first): one shipment after the other.
    assert kinds[0][0] == kinds[1][0] and kinds[2][0] == kinds[3][0]


def test_a_cold_chain_pair_that_can_only_be_served_over_the_limit_is_infeasible() -> None:
    # Due in 6 h: one after the other misses the second deadline, and both picked up first
    # rides the second 4 h 10 min. Without the cold chain both fit.
    plain = [order(1, deadline_h=6), order(2, deadline_h=6)]
    both = plan(plain, PAIR, NOW, IST)
    assert both.infeasible == [] and len(both.stops) == 4
    cold = [order(1, deadline_h=6, cold=True), order(2, deadline_h=6, cold=True)]
    result = plan(cold, PAIR, NOW, IST)
    (bad,) = result.infeasible
    assert bad.reason == (
        "The route search (5 s) found no order that also reaches "
        f"Hospital {bad.shipment_id.int} by 17:30 IST within the 4 h cold-chain ride limit "
        "with the other shipments in this route; plan it separately."
    )
    assert len(result.stops) == 2
    check_windows(result, cold)


def test_a_cold_chain_shipment_and_a_vehicle_without_cold_chain() -> None:
    orders = [order(1, deadline_h=6, cold=True), order(2, deadline_h=6)]
    result = plan(orders, uniform(4, 20), NOW, IST, vehicle=(False, "KA-01-VAN"))
    (bad,) = result.infeasible
    assert bad.reason == (
        "This shipment needs a cold-chain vehicle; vehicle KA-01-VAN has no cold chain."
    )
    assert {s.shipment_id for s in result.stops} == {orders[1].shipment_id}


# --- acceptance: ten shipments and the 5 s search limit ----------------------------------------


def bangalore(n: int) -> list[Order]:
    """`n` shipments around Bangalore, deadlines spread over the day, every third cold."""
    return [
        Order(
            shipment_id=uuid.UUID(int=k + 1),
            pickup_place=f"Source {k}",
            pickup=Point(12.85 + (k * 37 % 23) / 100, 77.45 + (k * 13 % 29) / 100),
            drop_place=f"Hospital {k}",
            drop=Point(12.85 + (k * 17 % 31) / 100, 77.45 + (k * 41 % 19) / 100),
            required_by=NOW + timedelta(hours=4 + k * 1.5),
            cold_chain=k % 3 == 0,
        )
        for k in range(n)
    ]


@pytest.mark.anyio
async def test_ten_shipments_solve_in_under_five_seconds() -> None:
    orders = bangalore(10)
    travel = await optimizer.duration_matrix(optimizer.points_of(orders), HaversineProvider())
    started = time.perf_counter()
    result = plan(orders, travel, NOW, IST)
    elapsed = time.perf_counter() - started
    assert elapsed < 5
    assert len(result.stops) + 2 * len(result.infeasible) == 20
    assert len(result.stops) >= 2
    check_windows(result, orders)


def test_the_search_limit_is_five_seconds() -> None:
    assert timedelta(seconds=5) == optimizer.SEARCH_LIMIT
    params = optimizer.search_parameters()
    assert params.time_limit.ToTimedelta() == timedelta(seconds=5)


def test_the_time_limit_stops_a_search_that_would_go_on() -> None:
    # Guided local search never stops by itself; the limit ends it with the best plan so far.
    orders = bangalore(12)
    points = optimizer.points_of(orders)
    travel = [[optimizer.travel_seconds(haversine_km(a, b) * 1.3) for b in points] for a in points]
    jobs = [Job(2 * k, 2 * k + 1, 24 * HOUR) for k in range(len(orders))]
    service = [HANDOVER if i % 2 == 0 else 0 for i in range(len(points))]
    started = time.perf_counter()
    solution = solve(travel, service, jobs, time_limit=timedelta(seconds=1), guided=True)
    elapsed = time.perf_counter() - started
    assert 0.9 <= elapsed < 2.5
    assert solution.left_out == [] and len(solution.visits) == 24


# --- pieces ---------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_duration_matrix_is_the_provider_table_at_forty_km_per_hour() -> None:
    class Table:
        calls = 0

        async def table(
            self, origins: Sequence[Point], dests: Sequence[Point]
        ) -> list[list[float]]:
            Table.calls += 1
            return [[0.0, 20.0], [10.0, 0.0]]

    a, b = Point(12.9, 77.5), Point(13.0, 77.6)
    out = await optimizer.duration_matrix([a, b], Table())  # type: ignore[arg-type]
    assert out == [[0, 30 * MIN], [15 * MIN, 0]]  # 20 km at 40 km/h = 30 min
    assert Table.calls == 1
    assert await optimizer.duration_matrix([], Table()) == []  # type: ignore[arg-type]


def test_spans_and_clock_times_read_plainly() -> None:
    assert optimizer.span(4 * HOUR) == "4 h"
    assert optimizer.span(4 * HOUR + 10 * MIN) == "4 h 10 min"
    assert optimizer.span(50 * MIN) == "50 min"
    assert optimizer.span(61) == "2 min"  # rounded up
    assert optimizer.clock(NOW + timedelta(hours=2), NOW, IST) == "13:30 IST"
    assert optimizer.clock(NOW, NOW, UTC) == "06:00 UTC"


def test_no_shipments_no_stops() -> None:
    assert plan([], [], NOW, UTC) == Plan([], [])


def test_a_route_greedy_descent_misses_is_found_by_the_guided_fallback() -> None:
    """From the S16 review: greedy descent alone serves only one of these three, though
    P0 -> P1 -> D1 -> D0 (and job 2 after) fits; plan() falls back to guided search."""
    travel = [
        [0, 4137, 1628, 3814, 3802, 4927],
        [4137, 0, 4101, 1332, 5662, 4405],
        [1628, 4101, 0, 3276, 2251, 3442],
        [3814, 1332, 3276, 0, 4485, 3076],
        [3802, 5662, 2251, 4485, 0, 2737],
        [4927, 4405, 3442, 3076, 2737, 0],
    ]
    deadlines = [14697, 15112, 11745]
    orders = [order(n, deadline_h=d / HOUR) for n, d in enumerate(deadlines)]
    greedy = solve(
        travel, [HANDOVER, 0] * 3, [Job(0, 1, 14697), Job(2, 3, 15112), Job(4, 5, 11745)]
    )
    assert greedy.left_out  # the fast pass alone misses a feasible route
    result = plan(orders, travel, NOW, IST, time_limit=timedelta(seconds=2))
    assert len(greedy.left_out) == 2  # greedy serves one
    assert [i.shipment_id for i in result.infeasible] == [uuid.UUID(int=2)]  # guided: two
    assert len(result.stops) == 4


def test_a_shipment_with_several_pickups_is_infeasible_with_the_reason() -> None:
    blocked = Order(**{**order(1, deadline_h=8).__dict__, "blocked": "several pickups"})
    result = plan([order(0, deadline_h=8), blocked], uniform(4, 20), NOW, IST)
    assert [(i.shipment_id, i.reason) for i in result.infeasible] == [
        (uuid.UUID(int=1), "several pickups")
    ]
    assert {s.shipment_id for s in result.stops} == {uuid.UUID(int=0)}


def test_an_earliest_arrival_seconds_past_the_deadline_rounds_up() -> None:
    at = NOW.replace(hour=8, minute=30, second=40)  # 14:00:40 IST
    assert optimizer.clock(at, NOW, IST, up=True) == "14:01 IST"
    assert optimizer.clock(at, NOW, IST) == "14:00 IST"
