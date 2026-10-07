from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role, User
from app.conftest import ClientFor, World, password_hash
from app.iot.models import Device
from app.orgs.models import Organization, OrgType

pytestmark = pytest.mark.anyio

SEEN = datetime(2026, 10, 7, 10, 15, tzinfo=UTC)


@pytest.fixture
async def fleet(session: AsyncSession) -> tuple[Organization, User]:
    """A logistics org with a dispatcher and two cold boxes."""
    org = Organization(name="SwiftMed Logistics", type=OrgType.LOGISTICS, lat=12.98, lng=77.64)
    session.add(org)
    await session.flush()
    dispatcher_role = await session.scalar(select(Role).where(Role.name == "DISPATCHER"))
    assert dispatcher_role is not None
    user = User(
        email="dispatcher@swiftmed.test",
        password_hash=password_hash(),
        full_name="Dispatcher L",
        org_id=org.id,
        org=org,
        roles=[dispatcher_role],
    )
    session.add_all(
        [
            user,
            Device(org_id=org.id, device_id="cb-01", battery_level=82, last_seen=SEEN),
            Device(org_id=org.id, device_id="cb-02"),
        ]
    )
    await session.flush()
    return org, user


async def test_dispatcher_lists_own_devices(
    client_for: ClientFor, fleet: tuple[Organization, User]
) -> None:
    org, dispatcher = fleet
    r = await (await client_for(dispatcher)).get("/devices")

    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [(d["device_id"], d["type"], d["battery_level"]) for d in items] == [
        ("cb-01", "COLD_BOX", 82),
        ("cb-02", "COLD_BOX", None),
    ]
    assert items[0]["last_seen"] == "2026-10-07T10:15:00Z"
    assert {d["org_id"] for d in items} == {str(org.id)}


async def test_devices_are_paginated(
    client_for: ClientFor, fleet: tuple[Organization, User]
) -> None:
    client = await client_for(fleet[1])
    first = (await client.get("/devices", params={"limit": 1})).json()
    second = (
        await client.get("/devices", params={"limit": 1, "cursor": first["next_cursor"]})
    ).json()

    assert [d["device_id"] for d in first["items"] + second["items"]] == ["cb-01", "cb-02"]
    assert second["next_cursor"] is None


async def test_another_orgs_dispatcher_sees_none_of_them(
    client_for: ClientFor, world: World, fleet: tuple[Organization, User]
) -> None:
    r = await (await client_for(world.users["s.DISPATCHER"])).get("/devices")
    assert r.status_code == 200
    assert r.json()["items"] == []


@pytest.mark.parametrize("role", ["a.STORE_MANAGER", "s.DRIVER", "b.APPROVER"])
async def test_devices_need_shipment_assign(
    client_for: ClientFor, world: World, fleet: tuple[Organization, User], role: str
) -> None:
    r = await (await client_for(world.users[role])).get("/devices")
    assert r.status_code == 403
    assert r.json()["details"] == {"capability": "shipment.assign"}


async def test_devices_need_sign_in(client_for: ClientFor) -> None:
    assert (await (await client_for()).get("/devices")).status_code == 401
