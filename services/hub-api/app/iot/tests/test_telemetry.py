from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.conftest import ClientFor, World
from app.iot.models import Device, SensorReading
from app.iot.schemas import MAX_BATCH
from app.orgs.models import Organization, OrgType

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 10, 7, 10, 15, tzinfo=UTC)


def reading(
    device: str = "cb-01", at: datetime = T0, temp: float = 4.31, **extra: Any
) -> dict[str, Any]:
    return {"device_id": device, "ts": at.isoformat(), "temp_c": temp, "battery": 82} | extra


@pytest.fixture
async def logistics(session: AsyncSession) -> Organization:
    org = Organization(name="SwiftMed Logistics", type=OrgType.LOGISTICS, lat=12.98, lng=77.64)
    session.add(org)
    await session.flush()
    return org


@pytest.fixture
async def cb01(session: AsyncSession, logistics: Organization) -> Device:
    device = Device(org_id=logistics.id, device_id="cb-01")
    session.add(device)
    await session.flush()
    return device


@pytest.fixture
async def ingest(client_for: ClientFor) -> httpx.AsyncClient:
    client = await client_for()
    client.headers["Authorization"] = f"Bearer {settings.ingest_token}"
    return client


async def stored(session: AsyncSession, device_id: str = "cb-01") -> list[SensorReading]:
    stmt = select(SensorReading).where(SensorReading.device_id == device_id)
    return list(await session.scalars(stmt.order_by(SensorReading.ts)))


async def test_stores_readings_and_refreshes_the_device(
    ingest: httpx.AsyncClient, session: AsyncSession, cb01: Device
) -> None:
    batch = [
        reading(at=T0, temp=4.31, battery=83),
        reading(at=T0 + timedelta(seconds=10), temp=4.5, battery=82),
        reading(at=T0 + timedelta(seconds=20), temp=4.7, battery=81),
    ]
    r = await ingest.post("/internal/telemetry", json={"readings": batch})
    assert r.status_code == 200, r.text
    assert r.json() == {"stored": 3, "duplicates": 0, "unknown_devices": []}

    rows = await stored(session)
    assert [(x.ts, x.temp_c, x.battery) for x in rows] == [
        (T0, 4.31, 83),
        (T0 + timedelta(seconds=10), 4.5, 82),
        (T0 + timedelta(seconds=20), 4.7, 81),
    ]
    await session.refresh(cb01)
    assert cb01.last_seen == T0 + timedelta(seconds=20)
    assert cb01.battery_level == 81


async def test_replaying_a_batch_adds_no_duplicates(
    ingest: httpx.AsyncClient, session: AsyncSession, cb01: Device
) -> None:
    batch = {"readings": [reading(at=T0 + timedelta(seconds=10 * i)) for i in range(5)]}
    first = await ingest.post("/internal/telemetry", json=batch)
    again = await ingest.post("/internal/telemetry", json=batch)

    assert first.json() == {"stored": 5, "duplicates": 0, "unknown_devices": []}
    assert again.status_code == 200
    assert again.json() == {"stored": 0, "duplicates": 5, "unknown_devices": []}
    assert len(await stored(session)) == 5


async def test_a_repeat_inside_one_batch_is_stored_once(
    ingest: httpx.AsyncClient, session: AsyncSession, cb01: Device
) -> None:
    r = await ingest.post("/internal/telemetry", json={"readings": [reading(), reading()]})
    assert r.json() == {"stored": 1, "duplicates": 1, "unknown_devices": []}
    assert len(await stored(session)) == 1


async def test_readings_from_unregistered_devices_are_not_stored(
    ingest: httpx.AsyncClient, session: AsyncSession, cb01: Device
) -> None:
    batch = [
        reading("cb-99"),
        reading("cb-01"),
        reading("cb-42"),
        reading("cb-99", T0 + timedelta(seconds=10)),
    ]
    r = await ingest.post("/internal/telemetry", json={"readings": batch})

    assert r.json() == {"stored": 1, "duplicates": 0, "unknown_devices": ["cb-42", "cb-99"]}
    assert len(await stored(session, "cb-01")) == 1
    assert await session.scalar(select(func.count()).select_from(Device)) == 1


async def test_late_buffered_readings_do_not_rewind_last_seen_or_battery(
    ingest: httpx.AsyncClient, session: AsyncSession, cb01: Device
) -> None:
    later = T0 + timedelta(minutes=5)
    await ingest.post("/internal/telemetry", json={"readings": [reading(at=later, battery=70)]})
    r = await ingest.post("/internal/telemetry", json={"readings": [reading(at=T0, battery=90)]})

    assert r.json()["stored"] == 1  # the late reading is still kept
    await session.refresh(cb01)
    assert (cb01.last_seen, cb01.battery_level) == (later, 70)


async def test_a_device_clock_ahead_of_the_hub_does_not_push_last_seen_into_the_future(
    ingest: httpx.AsyncClient, session: AsyncSession, cb01: Device
) -> None:
    ahead = datetime.now(UTC) + timedelta(hours=1)
    await ingest.post("/internal/telemetry", json={"readings": [reading(at=ahead)]})

    await session.refresh(cb01)
    assert cb01.last_seen is not None
    assert cb01.last_seen <= datetime.now(UTC)
    assert [x.ts for x in await stored(session)] == [ahead]  # the reading keeps its own ts


async def test_battery_is_optional(
    ingest: httpx.AsyncClient, session: AsyncSession, cb01: Device
) -> None:
    no_battery = {"device_id": "cb-01", "ts": T0.isoformat(), "temp_c": 4.0}
    r = await ingest.post("/internal/telemetry", json={"readings": [no_battery]})

    assert r.json()["stored"] == 1
    await session.refresh(cb01)
    assert (cb01.last_seen, cb01.battery_level) == (T0, None)
    assert (await stored(session))[0].battery is None


@pytest.mark.parametrize(
    "bad",
    [
        {"temp_c": 126},
        {"temp_c": "warm"},
        {"battery": 101},
        {"ts": "2026-10-07T10:15:00"},  # no timezone
        {"device_id": "cb/01"},
        {"device_id": ""},
        {"humidity": 40},
    ],
)
async def test_an_invalid_reading_is_422(
    ingest: httpx.AsyncClient, session: AsyncSession, cb01: Device, bad: dict[str, Any]
) -> None:
    r = await ingest.post("/internal/telemetry", json={"readings": [reading(), reading() | bad]})
    assert r.status_code == 422
    assert r.json()["code"] == "schema_error"
    assert await stored(session) == []


async def test_a_batch_over_the_limit_is_422(ingest: httpx.AsyncClient, cb01: Device) -> None:
    batch = [reading(at=T0 + timedelta(seconds=i)) for i in range(MAX_BATCH + 1)]
    r = await ingest.post("/internal/telemetry", json={"readings": batch})
    assert r.status_code == 422


async def test_telemetry_needs_the_ingest_token(
    client_for: ClientFor, world: World, session: AsyncSession, cb01: Device
) -> None:
    body = {"readings": [reading()]}
    anonymous = await client_for()
    assert (await anonymous.post("/internal/telemetry", json=body)).status_code == 401

    wrong = await client_for()
    wrong.headers["Authorization"] = f"Bearer {settings.ingest_token}x"
    assert (await wrong.post("/internal/telemetry", json=body)).status_code == 401

    for role in ("s.DISPATCHER", "p.ADMIN"):  # a signed-in user, even an admin, is refused
        user = await client_for(world.users[role])
        r = await user.post("/internal/telemetry", json=body)
        assert r.status_code == 401
        assert r.json()["code"] == "unauthenticated"
    assert await stored(session) == []
