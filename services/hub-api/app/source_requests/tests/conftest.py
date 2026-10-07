"""S06 fixtures. Scenario 1 (demo-scenarios.md) is built relative to the real clock, because
the endpoints read the clock; frozen-clock tests pass `now` to the service instead.

`committed_db` is a second, migrated database whose transactions really commit, for the
tests where two connections must race (row locks are invisible inside one transaction)."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.models import Role, User
from app.catalog.models import Product, ProductAuthorization, SupplierOffer
from app.conftest import HUB_DIR, TEST_DB_URL, World, password_hash
from app.inventory.models import InventoryBatch
from app.orgs.models import Facility, Organization, OrgType
from app.shortages import service as shortages
from app.shortages.models import Priority, Shortage
from app.shortages.schemas import ShortageCreate
from app.source_requests.models import SourceRequest

Orgs = dict[str, Organization]


async def facility_of(session: AsyncSession, org: Organization) -> Facility:
    facility = await session.scalar(select(Facility).where(Facility.org_id == org.id))
    assert facility is not None
    return facility


async def add_org(
    session: AsyncSession, name: str, org_type: OrgType, lat: float, lng: float
) -> Organization:
    org = Organization(name=name, type=org_type, lat=lat, lng=lng)
    session.add(org)
    await session.flush()
    session.add(
        Facility(org_id=org.id, name=name, address=name, lat=lat, lng=lng, has_cold_storage=True)
    )
    await session.flush()
    return org


async def add_user(session: AsyncSession, org: Organization, role: str, email: str) -> User:
    roles = {r.name: r for r in await session.scalars(select(Role))}
    user = User(
        email=email,
        password_hash=password_hash(),
        full_name=f"{role.title()} {org.name}",
        org_id=org.id,
        org=org,
        roles=[roles[role]],
    )
    session.add(user)
    await session.flush()
    return user


async def add_batch(
    session: AsyncSession,
    org: Organization,
    product: Product,
    now: datetime,
    *,
    on_hand: int,
    expiry_days: int,
    batch_no: str = "B-1",
    reserved: int = 0,
    allocated: int = 0,
    safety_stock: int = 0,
) -> InventoryBatch:
    batch = InventoryBatch(
        org_id=org.id,
        facility_id=(await facility_of(session, org)).id,
        product_id=product.id,
        batch_no=batch_no,
        on_hand=on_hand,
        reserved=reserved,
        allocated=allocated,
        safety_stock=safety_stock,
        expiry_date=now.date() + timedelta(days=expiry_days),
        unit_cost_paise=1500,
        last_verified_at=now - timedelta(hours=1),
    )
    session.add(batch)
    await session.flush()
    return batch


def add_offer(
    session: AsyncSession,
    org: Organization,
    product: Product,
    now: datetime,
    *,
    price: int,
    lead: int,
    qty: int,
) -> None:
    session.add(
        SupplierOffer(
            org_id=org.id,
            product_id=product.id,
            unit_price_paise=price,
            lead_time_hours=lead,
            available_qty=qty,
            updated_at=now - timedelta(hours=1),
        )
    )


def authorize(session: AsyncSession, product: Product, *orgs: Organization) -> None:
    session.add_all(ProductAuthorization(org_id=o.id, product_id=product.id) for o in orgs)


async def seed_scenario1(
    session: AsyncSession, a: Organization, b: Organization, ska: Product, now: datetime
) -> Orgs:
    """Scenario 1's seed state for Surgical Kit A around Hospital A (12.97, 77.59)."""
    c = await add_org(session, "Hospital C", OrgType.HOSPITAL, 13.02, 77.64)
    d = await add_org(session, "Hospital D", OrgType.HOSPITAL, 12.92, 77.55)
    e = await add_org(session, "Hospital E", OrgType.HOSPITAL, 13.00, 77.52)
    x = await add_org(session, "Supplier X", OrgType.SUPPLIER, 13.10, 77.59)
    y = await add_org(session, "Supplier Y", OrgType.SUPPLIER, 12.85, 77.66)
    await add_batch(session, a, ska, now, on_hand=150, expiry_days=100)
    await add_batch(  # 1,000 transferable
        session, b, ska, now, on_hand=2500, reserved=800, allocated=200, safety_stock=500,
        expiry_days=180,
    )  # fmt: skip
    await add_batch(  # 100 transferable
        session, c, ska, now, on_hand=1400, reserved=800, safety_stock=500, expiry_days=200
    )
    await add_batch(session, d, ska, now, on_hand=900, expiry_days=12)
    await add_batch(session, e, ska, now, on_hand=1200, expiry_days=150)
    add_offer(session, x, ska, now, price=1400, lead=66, qty=5000)
    add_offer(session, y, ska, now, price=2800, lead=22, qty=2000)
    authorize(session, ska, a, b, c, d, x, y)  # everyone except Hospital E
    await session.flush()
    return {o.name: o for o in (a, b, c, d, e, x, y)}


async def create_shortage(
    session: AsyncSession,
    user: User,
    product: Product,
    now: datetime,
    *,
    priority: Priority = Priority.CRITICAL,
    qty_required: int = 1000,
    qty_local_usable: int = 150,
) -> Shortage:
    """Scenario 1, step 1 by default: 1,000 required, 150 usable, CRITICAL, by now + 72 h."""
    org = await session.get_one(Organization, user.org_id)
    body = ShortageCreate(
        facility_id=(await facility_of(session, org)).id,
        product_id=product.id,
        qty_required=qty_required,
        qty_local_usable=qty_local_usable,
        required_by=now + timedelta(hours=72),
        priority=priority,
    )
    return await shortages.create_shortage(session, user, body, now=now)


async def requests_of(session: AsyncSession, shortage: Shortage) -> list[SourceRequest]:
    stmt = select(SourceRequest).where(SourceRequest.shortage_id == shortage.id)
    return list(await session.scalars(stmt.order_by(SourceRequest.created_at, SourceRequest.id)))


@pytest.fixture
def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
async def s1(
    session: AsyncSession, world: World, products: dict[str, Product], now: datetime
) -> Orgs:
    return await seed_scenario1(
        session, world.hospital_a, world.hospital_b, products["SURG-KIT-A"], now
    )


# --- a database whose transactions commit -----------------------------------------------------


@pytest.fixture(scope="session")
def committed_db_url() -> str:
    url = make_url(TEST_DB_URL)
    assert url.database and url.database.endswith("_test"), "refusing to drop a non-test DB"
    conc = url.set(database=url.database.removesuffix("_test") + "_conc_test")
    name = conc.database

    async def recreate() -> None:
        admin = create_async_engine(
            url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
        )
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
        await admin.dispose()

    asyncio.run(recreate())
    rendered = conc.render_as_string(hide_password=False)
    cfg = Config(HUB_DIR / "alembic.ini")
    cfg.set_main_option("sqlalchemy.url", rendered)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    return rendered


@pytest.fixture
async def committed(committed_db_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions on the committed database; every test adds its own uniquely named rows."""
    engine = create_async_engine(committed_db_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def unique() -> str:
    return os.urandom(4).hex()
