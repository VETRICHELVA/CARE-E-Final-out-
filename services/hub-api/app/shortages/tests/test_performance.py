"""S20 performance check: matching across 10 source orgs (7 hospitals, 3 suppliers) and all
40 products stays under 2 s per match run (the brief's bar), here for every product."""

import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Product, ProductAuthorization, SupplierOffer
from app.conftest import World
from app.inventory.models import InventoryBatch
from app.orgs.models import Facility, Organization, OrgType
from app.shortages import service
from app.shortages.models import Priority
from app.shortages.schemas import ShortageCreate
from app.source_requests.tests.conftest import facility_of

pytestmark = pytest.mark.anyio

NOW = datetime.now(UTC)
LIMIT_SECONDS = 2.0
HOSPITALS, SUPPLIERS, BATCHES_PER_PRODUCT = 7, 3, 3


async def network(session: AsyncSession, products: dict[str, Product]) -> list[Organization]:
    orgs = [
        Organization(name=f"Perf H{n}", type=OrgType.HOSPITAL, lat=12.9 + n / 100, lng=77.6)
        for n in range(HOSPITALS)
    ] + [
        Organization(name=f"Perf S{n}", type=OrgType.SUPPLIER, lat=13.0, lng=77.5 + n / 100)
        for n in range(SUPPLIERS)
    ]
    session.add_all(orgs)
    await session.flush()
    for org in orgs:
        session.add_all(
            ProductAuthorization(org_id=org.id, product_id=p.id) for p in products.values()
        )
        if org.type == OrgType.SUPPLIER:
            session.add_all(
                SupplierOffer(org_id=org.id, product_id=p.id, unit_price_paise=1000 + n * 10,
                              lead_time_hours=24 + n, available_qty=500,
                              updated_at=NOW - timedelta(hours=1))
                for n, p in enumerate(products.values())
            )  # fmt: skip
            continue
        facility = Facility(org_id=org.id, name=org.name, address=org.name, lat=org.lat,
                            lng=org.lng, has_cold_storage=True)  # fmt: skip
        session.add(facility)
        await session.flush()
        session.add_all(
            InventoryBatch(org_id=org.id, facility_id=facility.id, product_id=p.id,
                           batch_no=f"P-{b}", on_hand=400, safety_stock=100,
                           expiry_date=NOW.date() + timedelta(days=120 + 30 * b),
                           unit_cost_paise=900, last_verified_at=NOW - timedelta(hours=1))
            for p in products.values()
            for b in range(BATCHES_PER_PRODUCT)
        )  # fmt: skip
    await session.flush()
    return orgs


async def test_matching_across_10_orgs_and_40_products_is_under_2_seconds(
    session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    assert len(products) == 40
    await network(session, products)
    session.add_all(
        ProductAuthorization(org_id=world.hospital_a.id, product_id=p.id) for p in products.values()
    )
    await session.flush()
    facility = await facility_of(session, world.hospital_a)
    user = world.users["a.REQUESTER"]
    timings = []
    for product in products.values():
        body = ShortageCreate(
            facility_id=facility.id, product_id=product.id, qty_required=1500,
            qty_local_usable=0, required_by=NOW + timedelta(hours=72),
            priority=Priority.ROUTINE,
        )  # fmt: skip
        start = time.perf_counter()
        shortage = await service.create_shortage(session, user, body, now=NOW)
        timings.append(time.perf_counter() - start)
        run = await service.latest_run(session, shortage.id)
        assert run is not None
        out = await service.match_run_out(session, run)
        # 7 hospitals + 3 suppliers checked, at least.
        assert (
            len(
                {c.source_org_name for c in out.candidates}
                & {
                    *(f"Perf H{n}" for n in range(HOSPITALS)),
                    *(f"Perf S{n}" for n in range(SUPPLIERS)),
                }
            )
            == HOSPITALS + SUPPLIERS
        )
    print(f"\nmatch run seconds: max {max(timings):.3f}, total {sum(timings):.2f} for 40")
    assert max(timings) < LIMIT_SECONDS, timings
