"""S20 hardening checks that need no database: start-up secrets, CORS, the client address
behind proxies, webhook target rules, log redaction and the demo-reset guard."""

import ipaddress
import logging

import httpx
import pytest
from app import log, net
from app.config import DEV_AI_SERVICE_TOKEN, DEV_INGEST_TOKEN, DEV_JWT_SECRET, Settings
from app.demo_reset import refusal
from app.main import create_app
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.anyio

REAL = "x" * 40


def settings(**values: str) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


def test_dev_starts_with_the_committed_defaults() -> None:
    s = settings(app_env="dev")
    assert (s.jwt_secret, s.ingest_token, s.ai_service_token) == (
        DEV_JWT_SECRET, DEV_INGEST_TOKEN, DEV_AI_SERVICE_TOKEN,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("values", "named"),
    [
        ({}, ["JWT_SECRET", "INGEST_TOKEN", "AI_SERVICE_TOKEN"]),
        ({"jwt_secret": REAL, "ingest_token": REAL}, ["AI_SERVICE_TOKEN"]),
        ({"jwt_secret": "short", "ingest_token": REAL, "ai_service_token": REAL}, ["JWT_SECRET"]),
        ({"jwt_secret": REAL, "ingest_token": DEV_INGEST_TOKEN, "ai_service_token": REAL},
         ["INGEST_TOKEN"]),
    ],
)  # fmt: skip
def test_outside_dev_the_hub_refuses_default_or_short_secrets(
    values: dict[str, str], named: list[str]
) -> None:
    with pytest.raises(ValueError) as e:
        settings(app_env="production", **values)
    for name in named:
        assert name in str(e.value)
    for name in {"JWT_SECRET", "INGEST_TOKEN", "AI_SERVICE_TOKEN"} - set(named):
        assert name not in str(e.value)


def test_outside_dev_real_secrets_start() -> None:
    s = settings(app_env="production", jwt_secret=REAL, ingest_token=REAL, ai_service_token=REAL)
    assert not s.is_dev


async def test_cors_allows_only_the_three_apps() -> None:
    transport = httpx.ASGITransport(create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for port in (5173, 5174, 5175):
            origin = f"http://localhost:{port}"
            r = await client.options(
                "/api/v1/auth/me",
                headers={"Origin": origin, "Access-Control-Request-Method": "GET",
                         "Access-Control-Request-Headers": "authorization"},
            )  # fmt: skip
            assert r.status_code == 200
            assert r.headers["access-control-allow-origin"] == origin
        r = await client.options(
            "/api/v1/auth/me",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
        )
        assert r.status_code == 400
        assert "access-control-allow-origin" not in r.headers
        r = await client.get("/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in r.headers


TRUSTED = net.parse_networks("10.0.0.0/8, 192.0.2.1")


@pytest.mark.parametrize(
    ("peer", "forwarded", "client"),
    [
        ("203.0.113.9", None, "203.0.113.9"),
        ("203.0.113.9", "198.51.100.1", "203.0.113.9"),  # untrusted peer: header ignored
        ("10.0.0.5", "198.51.100.1", "198.51.100.1"),
        ("10.0.0.5", "1.1.1.1, 198.51.100.1", "198.51.100.1"),  # left-most is the client's
        ("10.0.0.5", "198.51.100.1, 192.0.2.1", "198.51.100.1"),  # two trusted hops
        ("10.0.0.5", "10.0.0.6", "10.0.0.6"),  # only proxies: the farthest
        ("10.0.0.5", " , ", "10.0.0.5"),
    ],
)
def test_client_ip(peer: str, forwarded: str | None, client: str) -> None:
    assert net.client_ip(peer, forwarded, TRUSTED) == client


@pytest.mark.parametrize(
    ("value", "public"),
    [("93.184.216.34", True), ("10.0.0.1", False), ("100.64.0.1", False),
     ("169.254.169.254", False), ("::1", False), ("::ffff:127.0.0.1", False),
     ("2606:4700::1111", True), ("224.0.0.1", False)],
)  # fmt: skip
def test_is_public(value: str, public: bool) -> None:
    assert net.is_public(ipaddress.ip_address(value)) is public


async def test_target_refusal_checks_every_resolved_address() -> None:
    async def resolver(host: str, port: int) -> list[str]:
        assert (host, port) == ("hooks.example.test", 443)
        return ["93.184.216.34", "fe80::1%eth0"]

    refusal_text = await net.target_refusal("https://hooks.example.test/x", resolver)
    assert refusal_text is not None and "fe80::1" in refusal_text

    async def public(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    assert await net.target_refusal("https://hooks.example.test/x", public) is None


def test_stream_tickets_never_reach_the_access_log() -> None:
    assert log.redact("/api/v1/events/stream?ticket=abc.def&x=1") == (
        "/api/v1/events/stream?ticket=[redacted]&x=1"
    )
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5", "GET", "/api/v1/events/stream?ticket=secret", "1.1", 200), None,
    )  # fmt: skip
    assert log.RedactQuerySecrets().filter(record)
    assert "secret" not in record.getMessage()
    assert "ticket=[redacted]" in record.getMessage()


@pytest.mark.parametrize(
    ("url", "env", "allowed"),
    [
        ("postgresql+asyncpg://care:care@127.0.0.1:5434/care", "dev", True),
        ("postgresql+asyncpg://care:care@localhost/care", "dev", True),
        ("postgresql+asyncpg://care:care@127.0.0.1:5434/care", "production", False),
        ("postgresql+asyncpg://care:care@db.internal:5432/care", "dev", False),
        ("postgresql+asyncpg://care:care@127.0.0.1:5434/care_test", "dev", False),
        ("postgresql+asyncpg://care:care@127.0.0.1:5434/postgres", "dev", False),
    ],
)
def test_demo_reset_wipes_only_the_local_dev_database(url: str, env: str, allowed: bool) -> None:
    assert (refusal(make_url(url), env) is None) is allowed
