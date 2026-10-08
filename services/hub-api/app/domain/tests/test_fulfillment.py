"""S11 shipment rules (business-rules.md §8 Shipment, §4) as pure functions."""

import pytest

from app.domain import costing
from app.domain.costing import HaversineProvider, Point
from app.domain.fulfillment import (
    ACTIVE_SHIPMENT,
    DRIVER_TRANSITIONS,
    SHIPMENT_TRANSITIONS,
    ShipmentStatus,
    cold_chain_vehicle_refusal,
    driver_may_move,
)

S = ShipmentStatus


def test_the_driver_moves_one_step_at_a_time_and_only_within_section_8() -> None:
    for frm, to in DRIVER_TRANSITIONS.items():
        assert to <= SHIPMENT_TRANSITIONS[frm]
    assert driver_may_move(S.ASSIGNED, S.PICKED_UP)
    assert driver_may_move(S.PICKED_UP, S.IN_TRANSIT)
    assert driver_may_move(S.IN_TRANSIT, S.DELIVERED)


@pytest.mark.parametrize(
    ("frm", "to"),
    [
        (S.CREATED, S.PICKED_UP),  # skipped ASSIGNED
        (S.ASSIGNED, S.IN_TRANSIT),  # skipped PICKED_UP
        (S.PICKED_UP, S.DELIVERED),  # skipped IN_TRANSIT
        (S.CREATED, S.ASSIGNED),  # the dispatcher's assign
        (S.ASSIGNED, S.CREATED),  # the dispatcher's unassign
        (S.DELIVERED, S.RECONCILED),  # reconciliation (S12)
        (S.PICKED_UP, S.ASSIGNED),  # backwards
    ],
)
def test_the_driver_cannot_skip_go_back_or_take_the_dispatchers_steps(
    frm: ShipmentStatus, to: ShipmentStatus
) -> None:
    assert not driver_may_move(frm, to)


def test_readings_link_only_on_the_way() -> None:
    assert {S.ASSIGNED, S.PICKED_UP, S.IN_TRANSIT} == ACTIVE_SHIPMENT


def test_a_cold_chain_shipment_needs_a_cold_chain_vehicle() -> None:
    assert cold_chain_vehicle_refusal(False, False, "KA-1") is None
    assert cold_chain_vehicle_refusal(False, True, "KA-1") is None
    assert cold_chain_vehicle_refusal(True, True, "KA-1") is None
    assert cold_chain_vehicle_refusal(True, False, "KA-1") == (
        "This shipment needs a cold-chain vehicle; vehicle KA-1 has no cold chain."
    )


@pytest.mark.anyio
async def test_the_haversine_route_is_the_straight_line_and_its_table_matches() -> None:
    a, b, c = Point(12.97, 77.59), Point(12.93, 77.62), Point(13.02, 77.64)
    h = HaversineProvider()
    route = await h.route(a, b)
    assert route.distance_km == await h.distance_km(a, b)
    assert (route.path, route.provider) == ((a, b), "HAVERSINE")
    assert await h.table([a, b], [c]) == [[await h.distance_km(a, c)], [await h.distance_km(b, c)]]
    assert costing.geojson_line(route.path) == {
        "type": "LineString",
        "coordinates": [[77.59, 12.97], [77.62, 12.93]],
    }
