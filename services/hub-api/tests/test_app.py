import httpx
import pytest
from app.main import create_app
from fastapi import FastAPI
from sqlalchemy.exc import DataError, DBAPIError

pytestmark = pytest.mark.anyio


def _client(app: FastAPI | None = None) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app or create_app(), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_health() -> None:
    async with _client() as client:
        r = await client.get("/health")
    assert (r.status_code, r.json()) == (200, {"status": "ok"})


async def test_openapi_is_served_under_api_v1() -> None:
    async with _client() as client:
        r = await client.get("/api/v1/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["openapi"].startswith("3.1")
    assert {
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/orgs/{org_id}",
        "/api/v1/orgs/{org_id}/facilities",
        "/api/v1/audit",
    } <= set(spec["paths"])


async def test_request_id_is_echoed_or_generated() -> None:
    async with _client() as client:
        echoed = await client.get("/health", headers={"X-Request-ID": "abc"})
        generated = await client.get("/health")
    assert echoed.headers["x-request-id"] == "abc"
    assert len(generated.headers["x-request-id"]) == 36


async def test_errors_use_code_message_details() -> None:
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internals")

    async with _client(app) as client:
        not_found = await client.get("/nope")
        wrong_method = await client.delete("/health")
        schema = await client.post("/api/v1/auth/login", json={"email": "x"})
        crash = await client.get("/boom")

    assert (not_found.status_code, not_found.json()) == (
        404,
        {"code": "not_found", "message": "Not Found", "details": {}},
    )
    assert (wrong_method.status_code, wrong_method.json()["code"]) == (405, "method_not_allowed")
    assert schema.status_code == 422
    assert schema.json()["code"] == "schema_error"
    assert schema.json()["details"]["errors"][0]["loc"] == ["body", "password"]
    assert (crash.status_code, crash.json()) == (
        500,
        {"code": "internal_error", "message": "Something went wrong.", "details": {}},
    )


class _DriverError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"driver error {sqlstate}")
        self.sqlstate = sqlstate


async def test_database_data_errors_are_400_validation() -> None:
    """SQLSTATE class 22 (a value the database cannot store) is bad input, not a crash.
    asyncpg raises it as a plain DBAPIError, so the SQLSTATE decides; psycopg raises
    DataError. Any other database error stays a 500."""
    app = create_app()

    @app.get("/db/{sqlstate}")
    async def db(sqlstate: str) -> None:
        raise DBAPIError("SELECT 1", None, _DriverError(sqlstate))

    @app.get("/data-error")
    async def data_error() -> None:
        raise DataError("SELECT 1", None, Exception("value out of range"))

    async with _client(app) as client:
        bad_input = [
            await client.get("/db/22021"),  # NUL in text
            await client.get("/db/22000"),  # int4 overflow, raised by asyncpg itself
            await client.get("/data-error"),
        ]
        other = await client.get("/db/42P01")  # undefined table: a bug, not bad input
    for r in bad_input:
        assert (r.status_code, r.json()["code"]) == (400, "validation")
    assert (other.status_code, other.json()["code"]) == (500, "internal_error")
