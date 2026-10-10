"""S20: webhooks may not target internal addresses (SSRF), and outside dev must use https."""

from datetime import UTC, datetime

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import net
from app.config import settings
from app.conftest import ClientFor, World
from app.domain.events import EventType
from app.events import service as events
from app.events import webhooks
from app.events.models import WebhookDelivery

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)
STATUS = EventType.SHORTAGE_STATUS_CHANGED


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9000/hook",
        "https://localhost/hook",
        "https://api.localhost/hook",
        "https://10.1.2.3/hook",
        "https://192.168.0.10/hook",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/hook",
        "https://[::ffff:10.0.0.1]/hook",
        "https://0.0.0.0/hook",
    ],
)
async def test_internal_targets_are_refused(world: World, client_for: ClientFor, url: str) -> None:
    client = await client_for(world.users["a.ADMIN"])
    r = await client.post("/webhooks", json={"url": url, "event_types": [STATUS]})
    assert r.status_code == 400, r.text
    assert r.json()["details"]["reason"] == "webhook_target_not_allowed"


async def test_a_dev_hub_may_allow_a_local_receiver(
    world: World, client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "webhook_allow_private_targets", True)
    client = await client_for(world.users["a.ADMIN"])
    r = await client.post(
        "/webhooks", json={"url": "http://127.0.0.1:9000/hook", "event_types": [STATUS]}
    )
    assert r.status_code == 201, r.text
    # Outside dev the flag is ignored, and plain http is refused.
    monkeypatch.setattr(settings, "app_env", "production")
    for url in ("http://127.0.0.1:9000/hook", "http://hooks.example.test/x"):
        r = await client.post("/webhooks", json={"url": url, "event_types": [STATUS]})
        assert r.status_code == 400, r.text
    r = await client.post(
        "/webhooks", json={"url": "https://hooks.example.test/x", "event_types": [STATUS]}
    )
    assert r.status_code == 201, r.text


async def test_a_name_that_resolves_inside_is_not_called(
    session: AsyncSession, redis: Redis, world: World, client_for: ClientFor
) -> None:
    """A public-looking name re-pointed at an internal address after subscribing."""
    r = await (await client_for(world.users["a.ADMIN"])).post(
        "/webhooks", json={"url": "https://rebound.example.test/x", "event_types": [STATUS]}
    )
    assert r.status_code == 201, r.text
    await events.emit(session, STATUS, [world.hospital_a.id], {"shortage_id": "x"})
    await events.publish_pending(session, redis, now=T0)

    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    async def inside(host: str, port: int) -> list[str]:
        return ["93.184.216.34", "10.0.0.7"]

    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as http:
        assert await webhooks.deliver_due(session, http, now=T0, resolver=inside) == 1
    assert calls == []
    first = await session.scalar(select(WebhookDelivery).where(WebhookDelivery.attempt == 1))
    assert first is not None and first.response_code is None  # a failed attempt, retried later


async def test_a_delivery_connects_to_the_address_it_checked(
    session: AsyncSession, redis: Redis, world: World, client_for: ClientFor
) -> None:
    """DNS rebinding: the name answers a public address for the check and a private one
    afterwards. The hub resolves once and connects to the checked address, with the original
    name in the Host header and as the TLS server name."""
    r = await (await client_for(world.users["a.ADMIN"])).post(
        "/webhooks", json={"url": "https://rebind.example.test/x", "event_types": [STATUS]}
    )
    assert r.status_code == 201, r.text
    await events.emit(session, STATUS, [world.hospital_a.id], {"shortage_id": "x"})
    await events.publish_pending(session, redis, now=T0)

    answers = [["93.184.216.34"], ["10.0.0.7"]]
    lookups: list[str] = []

    async def rebinding(host: str, port: int) -> list[str]:
        lookups.append(host)
        return answers.pop(0)

    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as http:
        assert await webhooks.deliver_due(session, http, now=T0, resolver=rebinding) == 1
    assert lookups == ["rebind.example.test"]  # one lookup; the private answer is never used
    (request,) = calls
    assert (request.url.host, request.url.port, request.url.path) == ("93.184.216.34", None, "/x")
    assert request.headers["host"] == "rebind.example.test"
    assert request.extensions["sni_hostname"] == "rebind.example.test"
    assert request.headers["connection"] == "close"
    delivery = await session.scalar(select(WebhookDelivery))
    assert delivery is not None and delivery.response_code == 204


@pytest.mark.parametrize("nat64", ["64:ff9b::a00:7", "64:ff9b::5db8:d822", "64:ff9b:1::a00:7"])
async def test_a_name_that_resolves_to_nat64_is_not_called(
    session: AsyncSession, redis: Redis, world: World, client_for: ClientFor, nat64: str
) -> None:
    """A NAT64 gateway reaches any IPv4 address through these prefixes, internal ones too."""
    client = await client_for(world.users["a.ADMIN"])
    r = await client.post(
        "/webhooks", json={"url": "https://nat64.example.test/x", "event_types": [STATUS]}
    )
    assert r.status_code == 201, r.text
    await events.emit(session, STATUS, [world.hospital_a.id], {"shortage_id": "x"})
    await events.publish_pending(session, redis, now=T0)

    async def resolver(host: str, port: int) -> list[str]:
        return [nat64]

    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as http:
        assert await webhooks.deliver_due(session, http, now=T0, resolver=resolver) == 1
    assert calls == []
    refusal = await net.target_refusal("https://nat64.example.test/x", resolver)
    assert refusal is not None and "64:ff9b" in refusal
    # The literal address is refused when subscribing.
    r = await client.post(
        "/webhooks", json={"url": f"https://[{nat64}]/x", "event_types": [STATUS]}
    )
    assert r.status_code == 400, r.text
    assert r.json()["details"]["reason"] == "webhook_target_not_allowed"
