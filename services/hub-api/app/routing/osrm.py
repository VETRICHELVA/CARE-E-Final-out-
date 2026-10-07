"""OSRM road routing (S11), with the haversine fallback of business-rules.md §4.

OSRM's HTTP API (osrm-routed, compose profile `routing`, infra/osrm/README.md):
- route: GET /route/v1/driving/{lng},{lat};{lng},{lat}?overview=full&geometries=geojson
  -> {"code": "Ok", "routes": [{"distance": metres, "geometry": {"coordinates": [[lng, lat]]}}]}
- table: GET /table/v1/driving/{coords}?sources=..&destinations=..&annotations=distance
  -> {"code": "Ok", "distances": [[metres | null]]}
"""

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from app.domain.costing import HaversineProvider, Point, Route, RoutingProvider

log = logging.getLogger("app")


class OSRMUnavailable(Exception):
    """OSRM failed, timed out, answered something unusable, or is cooling down."""


def _coords(points: Sequence[Point]) -> str:
    return ";".join(f"{p.lng:.6f},{p.lat:.6f}" for p in points)


class OSRMProvider:
    """`RoutingProvider` backed by an OSRM server. Any error, a non-"Ok" answer, or no answer
    within `timeout` seconds (2 s by default) gives the `fallback`'s result (haversine), and
    OSRM is then skipped for `cooldown` seconds, so matching many batches against a down
    server costs one timeout, not one per batch. Unreachable pairs in a table (null) are
    filled from the fallback."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 2.0,
        cooldown: float = 30.0,
        fallback: RoutingProvider | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cooldown = cooldown
        self.fallback: RoutingProvider = fallback or HaversineProvider()
        self._transport = transport
        self._clock = clock
        self._skip_until = 0.0

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if self._clock() < self._skip_until:
            raise OSRMUnavailable("OSRM failed recently; using the fallback.")
        try:
            async with asyncio.timeout(self.timeout):
                async with httpx.AsyncClient(
                    transport=self._transport, timeout=self.timeout
                ) as client:
                    response = await client.get(f"{self.base_url}{path}", params=params)
                response.raise_for_status()
                body = response.json()
            if not isinstance(body, dict) or body.get("code") != "Ok":
                code = body.get("code") if isinstance(body, dict) else None
                raise OSRMUnavailable(f"OSRM answered {code!r}.")
            return body
        except (httpx.HTTPError, TimeoutError, ValueError, OSRMUnavailable) as e:
            self._skip_until = self._clock() + self.cooldown
            log.warning(
                "OSRM unavailable; using haversine",
                extra={"error": repr(e), "cooldown_s": self.cooldown},
            )
            raise OSRMUnavailable(str(e) or type(e).__name__) from e

    async def _route(self, origin: Point, dest: Point, *, geometry: bool) -> Route:
        params = (
            {"overview": "full", "geometries": "geojson"} if geometry else {"overview": "false"}
        )
        body = await self._get(f"/route/v1/driving/{_coords([origin, dest])}", params)
        try:
            best = body["routes"][0]
            distance_km = float(best["distance"]) / 1000
            path = (
                tuple(Point(float(lat), float(lng)) for lng, lat in best["geometry"]["coordinates"])
                if geometry
                else (origin, dest)
            )
        except (KeyError, IndexError, TypeError, ValueError) as e:
            self._skip_until = self._clock() + self.cooldown
            raise OSRMUnavailable(f"Unusable OSRM route: {e!r}") from e
        if len(path) < 2:
            path = (origin, dest)
        return Route(distance_km, path, "OSRM")

    async def distance_km(self, origin: Point, dest: Point) -> float:
        try:
            return (await self._route(origin, dest, geometry=False)).distance_km
        except OSRMUnavailable:
            return await self.fallback.distance_km(origin, dest)

    async def route(self, origin: Point, dest: Point) -> Route:
        try:
            return await self._route(origin, dest, geometry=True)
        except OSRMUnavailable:
            return await self.fallback.route(origin, dest)

    async def table(self, origins: Sequence[Point], dests: Sequence[Point]) -> list[list[float]]:
        if not origins or not dests:
            return [[] for _ in origins]
        n = len(origins)
        params = {
            "sources": ";".join(str(i) for i in range(n)),
            "destinations": ";".join(str(n + j) for j in range(len(dests))),
            "annotations": "distance",
        }
        try:
            body = await self._get(f"/table/v1/driving/{_coords([*origins, *dests])}", params)
        except OSRMUnavailable:
            return await self.fallback.table(origins, dests)
        try:
            rows: list[list[float | None]] = [
                [None if m is None else float(m) / 1000 for m in row] for row in body["distances"]
            ]
            if len(rows) != n or any(len(row) != len(dests) for row in rows):
                raise ValueError("OSRM table has the wrong shape.")
        except (KeyError, TypeError, ValueError):
            self._skip_until = self._clock() + self.cooldown
            return await self.fallback.table(origins, dests)
        return [
            [
                km if km is not None else await self.fallback.distance_km(origins[i], dests[j])
                for j, km in enumerate(row)
            ]
            for i, row in enumerate(rows)
        ]
