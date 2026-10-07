import pytest

from app.domain import config, costing
from app.domain.costing import Point

A = Point(12.97, 77.59)
NORTH = Point(13.07, 77.59)


def test_one_degree_of_latitude_is_about_111_km() -> None:
    assert costing.haversine_km(Point(12.0, 77.0), Point(13.0, 77.0)) == pytest.approx(
        111.19, abs=0.01
    )


@pytest.mark.anyio
async def test_haversine_provider_adds_the_road_factor() -> None:
    road = await costing.HaversineProvider().distance_km(A, NORTH)
    assert road == pytest.approx(costing.haversine_km(A, NORTH) * config.ROAD_FACTOR)
    assert config.ROAD_FACTOR == 1.3


def test_transport_eta_is_distance_over_40_kmh_plus_1_h_handover() -> None:
    assert costing.transport_eta_hours(40) == 2.0
    assert costing.transport_eta_hours(0) == 1.0


def test_transport_cost_is_25_rupees_per_km() -> None:
    assert costing.transport_cost_paise(10) == 25_000


@pytest.mark.parametrize(("value", "fee"), [(1_000, 20), (1_224, 24), (1_225, 25), (1_275, 26)])
def test_handling_fee_is_2_percent_rounded_half_up(value: int, fee: int) -> None:
    assert costing.handling_fee_paise(value) == fee


def test_hospital_landed_cost_adds_transport_and_handling_fee() -> None:
    # 850 x Rs 15 = Rs 12,750 of items, Rs 250 transport, 2% handling fee (Rs 255)
    cost = costing.landed_cost_paise([(1000, 1500)], 850, 25_000, hospital=True)
    assert cost == 1_275_000 + 25_000 + 25_500


def test_supplier_landed_cost_has_no_handling_fee() -> None:
    cost = costing.landed_cost_paise([(5000, 1400)], 850, 25_000, hospital=False)
    assert cost == 1_190_000 + 25_000


def test_item_value_takes_lots_in_order() -> None:
    assert costing.item_value_paise([(300, 1000), (600, 1200)], 500) == 300 * 1000 + 200 * 1200


def test_item_value_refuses_more_than_the_lots_hold() -> None:
    with pytest.raises(ValueError):
        costing.item_value_paise([(100, 1000)], 101)
