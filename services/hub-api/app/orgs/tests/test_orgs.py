import base64
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import org_scoped
from app.conftest import ClientFor, World
from app.orgs.models import Facility

pytestmark = pytest.mark.anyio


async def test_own_org_in_full(client_for: ClientFor, world: World) -> None:
    client = await client_for(world.users["a.REQUESTER"])
    r = await client.get(f"/orgs/{world.hospital_a.id}")
    assert r.status_code == 200
    assert r.json() == {
        "id": str(world.hospital_a.id),
        "name": "Hospital A",
        "type": "HOSPITAL",
        "status": "ACTIVE",
        "location": {"lat": 12.97, "lng": 77.59},
    }


@pytest.mark.parametrize("other", ["hospital_b", "supplier", "platform"])
async def test_other_org_shows_public_fields_only(
    client_for: ClientFor, world: World, other: str
) -> None:
    org = getattr(world, other)
    client = await client_for(world.users["a.ADMIN"])
    r = await client.get(f"/orgs/{org.id}")
    assert r.status_code == 200
    assert r.json() == {
        "name": org.name,
        "type": org.type,
        "location": {"lat": org.lat, "lng": org.lng},
    }


async def test_own_facilities_in_full(client_for: ClientFor, world: World) -> None:
    client = await client_for(world.users["b.APPROVER"])
    r = await client.get(f"/orgs/{world.hospital_b.id}/facilities")
    assert r.status_code == 200
    [item] = r.json()["items"]
    assert set(item) == {"id", "org_id", "name", "address", "has_cold_storage", "location"}
    assert r.json()["next_cursor"] is None


async def test_other_org_facilities_show_name_and_location_only(
    client_for: ClientFor, world: World
) -> None:
    client = await client_for(world.users["a.STORE_MANAGER"])
    r = await client.get(f"/orgs/{world.hospital_b.id}/facilities")
    assert r.status_code == 200
    assert r.json()["items"] == [
        {"name": "Hospital B main store", "location": {"lat": 12.93, "lng": 77.62}}
    ]


async def test_facilities_paginate_with_cursor(
    client_for: ClientFor, world: World, session: AsyncSession
) -> None:
    a = world.hospital_a
    for n in (2, 3):
        session.add(Facility(org_id=a.id, name=f"Annex {n}", address="x", lat=a.lat, lng=a.lng))
        await session.flush()
    client = await client_for(world.users["a.REQUESTER"])

    seen: list[str] = []
    cursor = None
    for _ in range(3):
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = (await client.get(f"/orgs/{a.id}/facilities", params=params)).json()
        seen += [f["name"] for f in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == ["Hospital A main store", "Annex 2", "Annex 3"]


def _b64(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode()).decode()


@pytest.mark.parametrize(
    "cursor",
    [
        "garbage",
        _b64("no-separator"),
        _b64(f"not-a-date|{uuid.UUID(int=1)}"),
        _b64("2026-01-01T00:00:00+00:00|not-a-uuid"),
        _b64(f"0001-01-01T00:00:00+14:00|{uuid.UUID(int=1)}"),  # parses, overflows in UTC
        _b64(f"9999-12-31T23:59:59-14:00|{uuid.UUID(int=1)}"),
    ],
)
async def test_bad_cursor_is_400_validation(
    client_for: ClientFor, world: World, cursor: str
) -> None:
    client = await client_for(world.users["a.REQUESTER"])
    r = await client.get(f"/orgs/{world.hospital_a.id}/facilities", params={"cursor": cursor})
    assert r.status_code == 400
    assert r.json() == {"code": "validation", "message": "Invalid cursor.", "details": {}}


async def test_unknown_org_is_404(client_for: ClientFor, world: World) -> None:
    client = await client_for(world.users["a.REQUESTER"])
    for path in (f"/orgs/{uuid.uuid4()}", f"/orgs/{uuid.uuid4()}/facilities"):
        r = await client.get(path)
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"


async def test_orgs_require_sign_in(client_for: ClientFor, world: World) -> None:
    client = await client_for()
    assert (await client.get(f"/orgs/{world.hospital_a.id}")).status_code == 401


async def test_org_scoped_limits_query_to_callers_org(world: World, session: AsyncSession) -> None:
    stmt = org_scoped(select(Facility), world.users["b.STORE_MANAGER"])
    rows = (await session.scalars(stmt)).all()
    assert [f.org_id for f in rows] == [world.hospital_b.id]
