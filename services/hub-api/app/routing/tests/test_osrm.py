"""OSRMProvider against a fake OSRM (httpx.MockTransport): route, distance and table from
OSRM's answers, and the haversine fallback on errors, unusable answers and timeouts."""

import asyncio
import json

import httpx
import pytest

from app import routing
from app.config import settings
from app.domain.costing import HaversineProvider, Point
from app.routing.osrm import OSRMProvider

pytestmark = pytest.mark.anyio

A = Point(12.97, 77.59)
B = Point(12.93, 77.62)
C = Point(13.02, 77.64)
HAVERSINE = HaversineProvider()
ROUTE = {
    "code": "Ok",
    "routes": [
        {
            "distance": 7350.4,
            "duration": 900.1,
            "geometry": {
                "type": "LineString",
                "coordinates": [[77.59, 12.97], [77.605, 12.95], [77.62, 12.93]],
            },
        }
    ],
}


class FakeOSRM:
    """Answers like osrm-routed, records each request, and can be told to misbehave."""

    def __init__(self, body: object = ROUTE, status: int = 200, delay: float = 0.0) -> None:
        self.body, self.status, self.delay = body, status, delay
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.body, Exception):
            raise self.body
        return httpx.Response(self.status, content=json.dumps(self.body).encode())


def provider(fake: FakeOSRM, **kwargs: float) -> OSRMProvider:
    return OSRMProvider("http://osrm.test/", transport=httpx.MockTransport(fake), **kwargs)  # type: ignore[arg-type]


async def test_route_uses_osrm_distance_and_geometry() -> None:
    fake = FakeOSRM()
    route = await provider(fake).route(A, B)
    assert route.provider == "OSRM"
    assert route.distance_km == pytest.approx(7.3504)
    assert route.path == (Point(12.97, 77.59), Point(12.95, 77.605), Point(12.93, 77.62))
    (req,) = fake.requests
    assert req.url.path == "/route/v1/driving/77.590000,12.970000;77.620000,12.930000"
    assert req.url.params["geometries"] == "geojson"
    assert req.url.params["overview"] == "full"


async def test_distance_uses_osrm_without_geometry() -> None:
    fake = FakeOSRM()
    assert await provider(fake).distance_km(A, B) == pytest.approx(7.3504)
    assert fake.requests[0].url.params["overview"] == "false"


async def test_table_asks_sources_against_destinations() -> None:
    fake = FakeOSRM({"code": "Ok", "distances": [[1000.0, 2500.0], [None, 400.0]]})
    out = await provider(fake).table([A, B], [B, C])
    assert out[0] == [pytest.approx(1.0), pytest.approx(2.5)]
    # An unreachable pair (null) is filled from haversine; the rest stays OSRM's.
    assert out[1] == [pytest.approx(await HAVERSINE.distance_km(B, B)), pytest.approx(0.4)]
    params = fake.requests[0].url.params
    assert (params["sources"], params["destinations"], params["annotations"]) == (
        "0;1",
        "2;3",
        "distance",
    )


@pytest.mark.parametrize(
    "fake",
    [
        FakeOSRM(httpx.ConnectError("connection refused")),
        FakeOSRM(status=500, body={"code": "Error"}),
        FakeOSRM({"code": "NoRoute", "message": "Impossible route between points"}),
        FakeOSRM({"code": "Ok", "routes": []}),
        FakeOSRM({"code": "Ok", "routes": [{"geometry": None}]}),
        FakeOSRM("not an object"),
    ],
    ids=["no-connection", "http-500", "no-route", "no-routes", "malformed", "not-json-object"],
)
async def test_any_failure_falls_back_to_haversine(fake: FakeOSRM) -> None:
    route = await provider(fake).route(A, B)
    assert route == await HAVERSINE.route(A, B)
    assert route.provider == "HAVERSINE"
    assert route.path == (A, B)
    fake2 = FakeOSRM(fake.body, fake.status)
    assert await provider(fake2).distance_km(A, B) == await HAVERSINE.distance_km(A, B)
    fake3 = FakeOSRM(fake.body, fake.status)
    assert await provider(fake3).table([A], [B, C]) == await HAVERSINE.table([A], [B, C])


async def test_a_slow_osrm_times_out_into_haversine() -> None:
    fake = FakeOSRM(delay=1.0)
    started = asyncio.get_running_loop().time()
    route = await provider(fake, timeout=0.05).route(A, B)
    assert asyncio.get_running_loop().time() - started < 0.9
    assert route.provider == "HAVERSINE"
    assert route.distance_km == pytest.approx(await HAVERSINE.distance_km(A, B))


async def test_the_default_timeout_is_2_seconds() -> None:
    assert OSRMProvider("http://osrm.test").timeout == 2.0
    assert settings.osrm_timeout_seconds == 2.0


async def test_after_a_failure_osrm_is_skipped_until_the_cooldown_ends() -> None:
    now = [100.0]
    fake = FakeOSRM(httpx.ConnectError("down"))
    osrm = OSRMProvider(
        "http://osrm.test",
        transport=httpx.MockTransport(fake),
        cooldown=30,
        clock=lambda: now[0],
    )
    await osrm.distance_km(A, B)
    await osrm.distance_km(A, C)
    assert len(fake.requests) == 1  # the second call did not wait on a down server
    fake.body = ROUTE
    now[0] += 31
    assert (await osrm.route(A, B)).provider == "OSRM"
    assert len(fake.requests) == 2


def test_osrm_is_used_only_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "osrm_url", "")
    assert isinstance(routing.default_provider(), HaversineProvider)
    monkeypatch.setattr(settings, "osrm_url", "http://127.0.0.1:5000")
    chosen = routing.default_provider()
    assert isinstance(chosen, OSRMProvider)
    assert (chosen.base_url, chosen.timeout) == ("http://127.0.0.1:5000", 2.0)
