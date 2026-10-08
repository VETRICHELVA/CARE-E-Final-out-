"""The ingest token has scope `telemetry.write` and opens nothing but POST /internal/telemetry.

Walks every endpoint in the real app's OpenAPI document, so endpoints added later are
covered automatically."""

import re
import uuid

import pytest
from fastapi import FastAPI

from app.config import settings
from app.conftest import ClientFor

pytestmark = pytest.mark.anyio

API = "/api/v1"
TELEMETRY = ("POST", f"{API}/internal/telemetry")
# Endpoints that take no bearer token at all: they authenticate by their body.
PUBLIC = {("POST", f"{API}/auth/{p}") for p in ("login", "refresh", "logout")}


HTTP_METHODS = {"get", "put", "post", "patch", "delete"}


def endpoints(app: FastAPI) -> list[tuple[str, str]]:
    """Every (METHOD, path) the hub publishes in its OpenAPI document."""
    return sorted(
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        if path.startswith(API) and not path.startswith(f"{API}/_test/")  # conftest's route
        for method in item
        if method in HTTP_METHODS
    )


async def test_the_ingest_token_is_refused_on_every_other_endpoint(
    app: FastAPI, client_for: ClientFor
) -> None:
    client = await client_for()
    client.headers["Authorization"] = f"Bearer {settings.ingest_token}"
    checked = []
    for method, path in endpoints(app):
        if (method, path) == TELEMETRY or (method, path) in PUBLIC:
            continue
        url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path).removeprefix(API)
        body: dict[str, object] | None = None if method in ("GET", "DELETE") else {}
        r = await client.request(method, url, json=body)
        assert r.status_code == 401, (method, path, r.status_code, r.text)
        assert r.json()["code"] == "unauthenticated"
        checked.append((method, path))

    assert len(checked) >= 20  # every user endpoint up to S05 and S14's GET /devices
    assert ("GET", f"{API}/devices") in checked


async def test_public_endpoints_grant_the_ingest_token_nothing(
    app: FastAPI, client_for: ClientFor
) -> None:
    """Login, refresh and logout ignore the bearer header; the token is no credential there."""
    assert set(endpoints(app)) >= PUBLIC
    client = await client_for()
    client.headers["Authorization"] = f"Bearer {settings.ingest_token}"
    token = settings.ingest_token
    login = await client.post("/auth/login", json={"email": "iot@ingest", "password": token})
    assert login.status_code == 401
    refresh = await client.post("/auth/refresh", json={"refresh_token": token})
    assert refresh.status_code == 401


async def test_the_telemetry_endpoint_is_the_only_one_it_opens(
    app: FastAPI, client_for: ClientFor
) -> None:
    assert TELEMETRY in endpoints(app)
    client = await client_for()
    client.headers["Authorization"] = f"Bearer {settings.ingest_token}"
    r = await client.post("/internal/telemetry", json={"readings": []})
    assert r.status_code == 200
    assert r.json() == {"stored": 0, "duplicates": 0, "unknown_devices": []}
