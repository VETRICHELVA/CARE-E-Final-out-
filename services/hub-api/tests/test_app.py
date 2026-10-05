import httpx
import pytest
from app.main import create_app
from fastapi import FastAPI

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
