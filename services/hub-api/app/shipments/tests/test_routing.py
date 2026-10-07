"""Assignment routes through OSRM when it answers, and through haversine when it fails or is
slow (business-rules.md §4). The OSRM here is a fake (httpx.MockTransport): no OSRM runs."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app import routing
from app.domain.costing import HaversineProvider, Point, transport_eta_hours
from app.routing.osrm import OSRMProvider
from app.shipments.models import Shipment
from app.shipments.tests.conftest import Fleet, assign

pytestmark = pytest.mark.anyio

B_STORE, A_STORE = Point(12.93, 77.62), Point(12.97, 77.59)


def fake_osrm(handler: httpx.MockTransport) -> OSRMProvider:
    return OSRMProvider("http://osrm.test", transport=handler)


def eta_window(before: datetime, after: datetime, km: float) -> tuple[datetime, datetime]:
    hours = timedelta(hours=transport_eta_hours(km))
    return before + hours, after + hours


async def test_assign_uses_the_osrm_route_when_it_answers(
    monkeypatch: pytest.MonkeyPatch,
    shipment: Shipment,
    dispatcher: httpx.AsyncClient,
    swiftmed: Fleet,
) -> None:
    coords = [[77.62, 12.93], [77.61, 12.945], [77.6, 12.96], [77.59, 12.97]]
    body = {"code": "Ok", "routes": [{"distance": 8200.0, "geometry": {"coordinates": coords}}]}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, content=json.dumps(body).encode())

    monkeypatch.setattr(routing, "ROUTING", fake_osrm(httpx.MockTransport(handler)))
    before = datetime.now(UTC)
    r = await assign(dispatcher, shipment, swiftmed)
    after = datetime.now(UTC)
    assert r.status_code == 200, r.text
    out = r.json()
    assert seen == ["/route/v1/driving/77.620000,12.930000;77.590000,12.970000"]
    assert (out["route_provider"], out["route_distance_km"]) == ("OSRM", 8.2)
    assert out["route_geometry"] == {"type": "LineString", "coordinates": coords}
    low, high = eta_window(before, after, 8.2)  # 8.2 / 40 + 1 h
    assert low <= datetime.fromisoformat(out["eta"]) <= high


@pytest.mark.parametrize("failure", ["error", "timeout"])
async def test_osrm_unavailable_the_eta_still_comes_back_through_haversine(
    monkeypatch: pytest.MonkeyPatch,
    shipment: Shipment,
    dispatcher: httpx.AsyncClient,
    swiftmed: Fleet,
    failure: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if failure == "error":
            raise httpx.ConnectError("connection refused")
        await asyncio.sleep(1)  # longer than the provider's timeout below
        return httpx.Response(200, content=b"{}")

    osrm = OSRMProvider("http://osrm.test", transport=httpx.MockTransport(handler), timeout=0.05)
    monkeypatch.setattr(routing, "ROUTING", osrm)
    before = datetime.now(UTC)
    r = await assign(dispatcher, shipment, swiftmed)
    after = datetime.now(UTC)
    assert r.status_code == 200, r.text
    out = r.json()
    km = await HaversineProvider().distance_km(B_STORE, A_STORE)
    assert out["route_provider"] == "HAVERSINE"
    assert out["route_distance_km"] == pytest.approx(km, abs=0.001)
    assert out["route_geometry"]["coordinates"] == [[77.62, 12.93], [77.59, 12.97]]
    low, high = eta_window(before, after, km)
    assert low <= datetime.fromisoformat(out["eta"]) <= high
