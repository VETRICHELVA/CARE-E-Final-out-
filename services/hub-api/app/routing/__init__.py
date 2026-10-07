"""The hub's road routing (business-rules.md §4): OSRM when `OSRM_URL` is set, falling back
to haversine x ROAD_FACTOR on error or timeout; haversine only when it is not set.

Matching and shipment assignment both call `routing.ROUTING` at call time, so tests can
swap it for a fake (the hub tests pin it to haversine)."""

from app.config import settings
from app.domain.costing import HaversineProvider, RoutingProvider
from app.routing.osrm import OSRMProvider


def default_provider() -> RoutingProvider:
    if settings.osrm_url:
        return OSRMProvider(
            settings.osrm_url,
            timeout=settings.osrm_timeout_seconds,
            cooldown=settings.osrm_cooldown_seconds,
        )
    return HaversineProvider()


ROUTING: RoutingProvider = default_provider()
