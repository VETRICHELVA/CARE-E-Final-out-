"""S20: the login rate limit keys on the client a trusted proxy saw, and its limit is
configurable (e2e runs sign in more than 5 times a minute)."""

import pytest

from app.config import settings
from app.conftest import PASSWORD, ClientFor, World

pytestmark = pytest.mark.anyio

BAD = {"email": "approver@a.test", "password": "wrong"}
GOOD = {"email": "approver@a.test", "password": PASSWORD}


async def test_behind_a_trusted_proxy_each_client_has_its_own_budget(
    client_for: ClientFor, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "trusted_proxies", "10.9.0.0/16")
    proxy = await client_for(ip="10.9.0.1")
    for _ in range(5):
        r = await proxy.post("/auth/login", json=BAD, headers={"X-Forwarded-For": "203.0.113.5"})
        assert r.status_code == 401
    r = await proxy.post("/auth/login", json=GOOD, headers={"X-Forwarded-For": "203.0.113.5"})
    assert r.status_code == 429
    # Another client behind the same proxy is not limited; a spoofed left-most hop is ignored.
    r = await proxy.post(
        "/auth/login", json=GOOD, headers={"X-Forwarded-For": "203.0.113.5, 203.0.113.6"}
    )
    assert r.status_code == 200, r.text


async def test_an_untrusted_peer_cannot_dodge_the_limit_with_the_header(
    client_for: ClientFor, world: World
) -> None:
    client = await client_for(ip="198.51.100.7")
    for n in range(5):
        headers = {"X-Forwarded-For": f"203.0.113.{n}"}
        assert (await client.post("/auth/login", json=BAD, headers=headers)).status_code == 401
    r = await client.post("/auth/login", json=GOOD, headers={"X-Forwarded-For": "203.0.113.99"})
    assert r.status_code == 429


async def test_the_limit_is_configurable(
    client_for: ClientFor, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_rate_limit", 8)
    client = await client_for(ip="10.0.0.42")
    for _ in range(8):
        assert (await client.post("/auth/login", json=BAD)).status_code == 401
    assert (await client.post("/auth/login", json=GOOD)).status_code == 429
