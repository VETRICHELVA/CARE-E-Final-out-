"""Distance, transport ETA and landed cost (business-rules.md §4). Money is integer paise."""

import math
from collections.abc import Sequence
from typing import NamedTuple, Protocol

from app.domain import config

EARTH_RADIUS_KM = 6371.0


class Point(NamedTuple):
    lat: float
    lng: float


class Route(NamedTuple):
    """A road route: its distance, the path as points in travel order, and which provider
    gave it ("OSRM", or "HAVERSINE" for the straight-line fallback)."""

    distance_km: float
    path: tuple[Point, ...]
    provider: str


class RoutingProvider(Protocol):
    async def distance_km(self, origin: Point, dest: Point) -> float: ...

    async def route(self, origin: Point, dest: Point) -> Route: ...

    async def table(
        self, origins: Sequence[Point], dests: Sequence[Point]
    ) -> list[list[float]]: ...


def haversine_km(a: Point, b: Point) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (a.lat, a.lng, b.lat, b.lng))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


class HaversineProvider:
    """Road distance estimated as straight-line distance x ROAD_FACTOR (§4): the fallback
    whenever OSRM is not configured, fails or times out. Its route is the straight line."""

    async def distance_km(self, origin: Point, dest: Point) -> float:
        return haversine_km(origin, dest) * config.ROAD_FACTOR

    async def route(self, origin: Point, dest: Point) -> Route:
        return Route(await self.distance_km(origin, dest), (origin, dest), "HAVERSINE")

    async def table(self, origins: Sequence[Point], dests: Sequence[Point]) -> list[list[float]]:
        """Distance (km) from each origin (rows) to each destination (columns)."""
        return [[await self.distance_km(o, d) for d in dests] for o in origins]


def geojson_line(path: Sequence[Point]) -> dict[str, object]:
    """A path as a GeoJSON LineString (coordinates are [lng, lat], as GeoJSON and Leaflet's
    GeoJSON layer expect)."""
    return {"type": "LineString", "coordinates": [[p.lng, p.lat] for p in path]}


def transport_eta_hours(distance_km: float) -> float:
    return distance_km / config.AVG_SPEED_KMH + config.HANDOVER_HOURS


def transport_cost_paise(distance_km: float) -> int:
    return round(distance_km * config.TRANSPORT_RATE_PAISE_PER_KM)


def handling_fee_paise(item_value_paise: int) -> int:
    """HANDLING_FEE_PCT of the item value, rounded half up."""
    return (item_value_paise * config.HANDLING_FEE_PCT + 50) // 100


def item_value_paise(lots: Sequence[tuple[int, int]], qty: int) -> int:
    """Take `qty` from `lots` of (qty, unit price paise) in order."""
    value, left = 0, qty
    for lot_qty, unit in lots:
        take = min(lot_qty, left)
        value, left = value + take * unit, left - take
    if left:
        raise ValueError(f"Lots hold {qty - left}, not {qty}.")
    return value


def landed_cost_paise(
    lots: Sequence[tuple[int, int]], qty: int, transport_paise: int, hospital: bool
) -> int:
    """Hospital: items + transport + handling fee. Supplier: items + transport."""
    items = item_value_paise(lots, qty)
    return items + transport_paise + (handling_fee_paise(items) if hospital else 0)
