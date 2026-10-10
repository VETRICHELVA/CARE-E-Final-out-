"""Shared fixtures: a migrated `care_test` database, a rolled-back session per test,
two hospitals + one supplier (+ the platform org) with a user per role, and a client factory.

Needs `make up` (Postgres on 5434, Redis on 6379). Override with TEST_DATABASE_URL / TEST_REDIS_URL.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app import routing
from app.auth import service as auth_service
from app.auth.models import Role, User
from app.catalog.models import Product
from app.catalog.service import seed_catalog
from app.db import get_session
from app.dbrole import APP_ROLE
from app.domain.costing import HaversineProvider
from app.domain.state_machine import transition
from app.main import create_app
from app.orgs.models import Facility, Organization, OrgType

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://care:care@127.0.0.1:5434/care_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
PASSWORD = "correct-horse-battery"
HUB_DIR = Path(__file__).resolve().parent.parent


@cache
def password_hash() -> str:
    return auth_service.hash_password(PASSWORD)  # argon2 is slow on purpose; hash once


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def haversine_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests measure distances by haversine x ROAD_FACTOR (business-rules.md §4) even when a
    developer's .env points OSRM_URL at a running OSRM; OSRM tests use a fake OSRM."""
    monkeypatch.setattr(routing, "ROUTING", HaversineProvider())


@pytest.fixture(scope="session")
def migrated_db() -> None:
    """Recreate the test database and run every migration, including the audit trigger."""
    url = make_url(TEST_DB_URL)
    assert url.database and url.database.endswith("_test"), "refusing to drop a non-test DB"

    async def recreate() -> None:
        admin = create_async_engine(
            url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
        )
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
        await admin.dispose()

    asyncio.run(recreate())
    cfg = Config(HUB_DIR / "alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_DB_URL)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


@pytest.fixture
async def session(migrated_db: None) -> AsyncIterator[AsyncSession]:
    """Each test runs in one outer transaction that is rolled back; app commits become
    savepoint releases. It runs as the hub's least-privilege role `care_app` (0017_s20fix),
    as the hub does outside dev; `as_owner` switches back for a test of the tables' owner."""
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.connect() as conn:
        outer = await conn.begin()
        await conn.exec_driver_sql(f"SET LOCAL ROLE {APP_ROLE}")
        s = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield s
        finally:
            await s.close()
            await outer.rollback()
    await engine.dispose()


async def as_owner(session: AsyncSession) -> None:
    """The rest of the test's transaction runs as the tables' owner (the test login, `care`),
    e.g. to show that a trigger stops even the owner."""
    await session.execute(text("RESET ROLE"))


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    r = Redis.from_url(TEST_REDIS_URL)
    await r.flushdb()
    yield r
    await r.aclose()


class _Thing(BaseModel):
    status: str


# Test-only route: S02 has no domain state machine endpoint yet, so this proves the
# InvalidTransition -> 409 mapping through the real app stack.
_test_router = APIRouter()


@_test_router.post("/_test/transition")
async def _transition(status: str, to: str) -> _Thing:
    thing = _Thing(status=status)
    transition(thing, to, {"DRAFT": {"OPEN"}, "OPEN": {"CLOSED"}})
    return thing


@pytest.fixture
def app(session: AsyncSession, redis: Redis) -> FastAPI:
    app = create_app()
    app.include_router(_test_router, prefix="/api/v1")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[auth_service.get_redis] = lambda: redis
    return app


@dataclass
class World:
    hospital_a: Organization
    hospital_b: Organization
    supplier: Organization
    platform: Organization
    users: dict[str, User]  # "<org key>.<ROLE>", e.g. "a.APPROVER"


ORG_ROLES = {
    "a": ["STORE_MANAGER", "REQUESTER", "APPROVER", "RECEIVER", "ADMIN"],
    "b": ["STORE_MANAGER", "APPROVER"],
    "s": ["SUPPLIER_DESK", "DISPATCHER", "DRIVER", "ADMIN"],
    "p": ["ADMIN"],
}


@pytest.fixture
async def world(session: AsyncSession) -> World:
    roles = {r.name: r for r in await session.scalars(select(Role))}
    orgs = {
        "a": Organization(name="Hospital A", type=OrgType.HOSPITAL, lat=12.97, lng=77.59),
        "b": Organization(name="Hospital B", type=OrgType.HOSPITAL, lat=12.93, lng=77.62),
        "s": Organization(name="Supplier S", type=OrgType.SUPPLIER, lat=13.01, lng=77.55),
        "p": Organization(name="CARE-E Platform", type=OrgType.PLATFORM, lat=12.97, lng=77.59),
    }
    session.add_all(orgs.values())
    await session.flush()
    for org in orgs.values():
        session.add(
            Facility(
                org_id=org.id,
                name=f"{org.name} main store",
                address=f"1 Main Road, {org.name}",
                lat=org.lat,
                lng=org.lng,
                has_cold_storage=True,
            )
        )
    users = {
        f"{key}.{role}": User(
            email=f"{role.lower()}@{key}.test",
            password_hash=password_hash(),
            full_name=f"{role.title()} {key.upper()}",
            org_id=orgs[key].id,
            org=orgs[key],
            roles=[roles[role]],
        )
        for key, names in ORG_ROLES.items()
        for role in names
    }
    session.add_all(users.values())
    await session.flush()
    return World(orgs["a"], orgs["b"], orgs["s"], orgs["p"], users)


@pytest.fixture
async def products(session: AsyncSession) -> dict[str, Product]:
    """The real seed catalog (scripts/seed/catalog.py), keyed by product code."""
    return await seed_catalog(session)


ClientFor = Callable[..., Awaitable[httpx.AsyncClient]]


@pytest.fixture
async def client_for(app: FastAPI, session: AsyncSession) -> AsyncIterator[ClientFor]:
    """`await client_for(user)` -> a client signed in as `user`; `client_for()` -> anonymous."""
    clients: list[httpx.AsyncClient] = []

    async def make(user: User | None = None, ip: str = "127.0.0.1") -> httpx.AsyncClient:
        headers = {}
        if user is not None:
            pair = await auth_service.issue_tokens(session, user)
            headers["Authorization"] = f"Bearer {pair.access_token}"
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app, client=(ip, 1)),
            base_url="http://test/api/v1",
            headers=headers,
        )
        clients.append(client)
        return client

    yield make
    for c in clients:
        await c.aclose()
