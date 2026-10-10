"""S18 helpers shared by the surplus and forecasting tests: extra hospitals, IV Cannula 20G
batches, stored forecasts and open shortages, all relative to the real UTC date (the
routers read the clock)."""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role, User
from app.catalog.models import Product
from app.conftest import password_hash
from app.forecasting.models import Forecast
from app.inventory.models import InventoryBatch
from app.orgs.models import Facility, Organization, OrgType
from app.shortages.models import Shortage


def today() -> date:
    return datetime.now(UTC).date()


async def facility_of(session: AsyncSession, org: Organization) -> Facility:
    facility = await session.scalar(select(Facility).where(Facility.org_id == org.id))
    assert facility is not None
    return facility


async def add_hospital(
    session: AsyncSession,
    name: str,
    lat: float,
    lng: float,
    *,
    org_id: uuid.UUID | None = None,
    roles: tuple[str, ...] = ("STORE_MANAGER",),
) -> tuple[Organization, dict[str, User]]:
    org = Organization(name=name, type=OrgType.HOSPITAL, lat=lat, lng=lng)
    if org_id is not None:
        org.id = org_id
    session.add(org)
    await session.flush()
    session.add(
        Facility(
            org_id=org.id,
            name=f"{name} central store",
            address=name,
            lat=lat,
            lng=lng,
            has_cold_storage=True,
        )
    )
    all_roles = {r.name: r for r in await session.scalars(select(Role))}
    slug = name.lower().replace(" ", "-")
    users = {
        role: User(
            email=f"{role.lower()}@{slug}.test",
            password_hash=password_hash(),
            full_name=f"{name} {role}",
            org_id=org.id,
            org=org,
            roles=[all_roles[role]],
        )
        for role in roles
    }
    session.add_all(users.values())
    await session.flush()
    return org, users


async def add_batch(
    session: AsyncSession,
    org: Organization,
    product: Product,
    *,
    on_hand: int,
    expiry_days: int,
    safety_stock: int = 0,
    reserved: int = 0,
    batch_no: str = "IV-1",
    unit_cost_paise: int = 500,
) -> InventoryBatch:
    batch = InventoryBatch(
        org_id=org.id,
        facility_id=(await facility_of(session, org)).id,
        product_id=product.id,
        batch_no=batch_no,
        on_hand=on_hand,
        safety_stock=safety_stock,
        reserved=reserved,
        expiry_date=today() + timedelta(days=expiry_days),
        unit_cost_paise=unit_cost_paise,
        last_verified_at=datetime.now(UTC) - timedelta(hours=1),
    )
    session.add(batch)
    await session.flush()
    return batch


async def add_forecast(
    session: AsyncSession, org: Organization, product: Product, per_day: float, days: int = 30
) -> None:
    """A stored flat forecast from today (as a run would leave it)."""
    session.add_all(
        Forecast(
            org_id=org.id,
            product_id=product.id,
            date=today() + timedelta(days=i),
            predicted_qty=per_day,
            lower=per_day,
            upper=per_day,
            model_version="test/1",
        )
        for i in range(days)
    )
    await session.flush()


async def add_shortage(
    session: AsyncSession, org: Organization, user: User, product: Product, status: str = "MATCHING"
) -> Shortage:
    """An open shortage row as matching would leave it (no match run needed here)."""
    shortage = Shortage(
        org_id=org.id,
        facility_id=(await facility_of(session, org)).id,
        product_id=product.id,
        qty_required=300,
        qty_local_usable=0,
        shortfall=300,
        required_by=datetime.now(UTC) + timedelta(days=3),
        priority="ROUTINE",
        min_shelf_life_days=30,
        status=status,
        created_by=user.id,
        source="FORM",
    )
    session.add(shortage)
    await session.flush()
    return shortage
