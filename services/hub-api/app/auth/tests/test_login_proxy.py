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


@pytest.mark.parametrize(("limit", "window"), [(100, 60), (5, 1)])
async def test_outside_dev_the_limit_cannot_be_loosened(
    client_for: ClientFor, world: World, monkeypatch: pytest.MonkeyPatch, limit: int, window: int
) -> None:
    """A LOGIN_RATE_LIMIT above 5 (or a window under 60 s) is a dev-only setting."""
    monkeypatch.setattr(settings, "login_rate_limit", limit)
    monkeypatch.setattr(settings, "login_rate_window_seconds", window)
    monkeypatch.setattr(settings, "app_env", "production")
    client = await client_for(ip="10.0.0.43")
    for _ in range(5):
        assert (await client.post("/auth/login", json=BAD)).status_code == 401
    r = await client.post("/auth/login", json=GOOD)
    assert r.status_code == 429
    assert r.json()["details"]["retry_after"] > 1


async def test_a_client_line_of_x_forwarded_for_does_not_reset_its_budget(
    client_for: ClientFor, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client sends its own X-Forwarded-For line; the proxy appends the address it saw
    in a second line. Every line counts, so the client stays the proxy's 203.0.113.5."""
    monkeypatch.setattr(settings, "trusted_proxies", "10.9.0.0/16")
    proxy = await client_for(ip="10.9.0.1")
    for n in range(5):
        headers = [("X-Forwarded-For", f"198.51.100.{n}"), ("X-Forwarded-For", "203.0.113.5")]
        assert (await proxy.post("/auth/login", json=BAD, headers=headers)).status_code == 401
    headers = [("X-Forwarded-For", "198.51.100.99"), ("X-Forwarded-For", "203.0.113.5")]
    assert (await proxy.post("/auth/login", json=GOOD, headers=headers)).status_code == 429
