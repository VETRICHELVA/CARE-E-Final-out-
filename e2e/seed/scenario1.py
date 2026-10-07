"""Scenario 1 seed state for the Playwright test (docs/specs/demo-scenarios.md), on top of the
minimal dev seed. The dev seed has no batches, offers or authorizations yet (follow-up S04 → S20),
so this adds them; S20's full seed replaces this script.

Run from services/hub-api, against the database the hub under test uses:

    cd services/hub-api && uv run python ../../e2e/seed/scenario1.py

Idempotent, and it resets Scenario 1 each time:
- adds Hospital C, D, E and Supplier Y (with a facility each) if missing;
- writes one `E2E-S1` Surgical Kit A batch per hospital with the scenario's numbers, verified
  an hour ago, and the X and Y offers, updated now (times are relative to now, as the
  freshness gate needs);
- authorizes everyone for Surgical Kit A except Hospital E;
- cancels Hospital A's open Surgical Kit A shortages from earlier runs through the hub's own
  service, which supersedes their source requests and releases their holds (with audit rows).
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "hub-api"))

from app.auth.models import User  # noqa: E402
from app.catalog.models import Product, ProductAuthorization, SupplierOffer  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.inventory.models import InventoryBatch  # noqa: E402
from app.orgs.models import Facility, Organization, OrgType  # noqa: E402
from app.seed import seed  # noqa: E402
from app.shortages import service as shortages  # noqa: E402
from app.shortages.models import Shortage  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

BATCH_NO = "E2E-S1"
CANCELLABLE = ("OPEN", "MATCHING", "AWAITING_DECISION")

# name, type, lat, lng: within 5-40 km of Hospital A (12.9592, 77.6974)
EXTRA_ORGS = [
    ("Hospital C", OrgType.HOSPITAL, 13.0100, 77.6500),
    ("Hospital D", OrgType.HOSPITAL, 12.9000, 77.6600),
    ("Hospital E", OrgType.HOSPITAL, 13.0000, 77.7400),
    ("Supplier Y", OrgType.SUPPLIER, 12.8500, 77.6600),
]

# hospital: on_hand, reserved, allocated, safety_stock, expiry in days (demo-scenarios.md)
BATCHES = {
    "Hospital B": (2500, 800, 200, 500, 180),  # 1,000 transferable
    "Hospital C": (1400, 800, 0, 500, 200),  # 100 transferable
    "Hospital D": (900, 0, 0, 0, 12),  # 900 transferable, expires too soon
    "Hospital E": (1200, 0, 0, 0, 150),  # 1,200 transferable, not authorized
}

# supplier: unit price in paise, lead time in hours, available qty
OFFERS = {"Supplier X": (1400, 66, 5000), "Supplier Y": (2800, 22, 2000)}

AUTHORIZED = ["Hospital A", "Hospital B", "Hospital C", "Hospital D", "Supplier X", "Supplier Y"]


async def ensure_org(session: AsyncSession, name: str, org_type: OrgType, lat: float, lng: float):
    org = await session.scalar(select(Organization).where(Organization.name == name))
    if org is None:
        org = Organization(name=name, type=org_type, lat=lat, lng=lng)
        session.add(org)
        await session.flush()
        if org_type == OrgType.HOSPITAL:
            session.add(
                Facility(
                    org_id=org.id,
                    name=f"{name} central store",
                    address=f"{name}, Bengaluru",
                    lat=lat,
                    lng=lng,
                    has_cold_storage=name == "Hospital C",
                )
            )
            await session.flush()
    return org


async def cancel_open_shortages(session: AsyncSession, a: Organization, product: Product) -> int:
    """Earlier runs' shortages would keep asking Hospital B; cancel them as Hospital A."""
    user = await session.scalar(
        select(User).where(User.org_id == a.id, User.email.like("store.manager@%"))
    )
    assert user is not None, "Hospital A's store manager is missing; run `make seed` first"
    stmt = select(Shortage.id).where(
        Shortage.org_id == a.id,
        Shortage.product_id == product.id,
        Shortage.status.in_(CANCELLABLE),
    )
    ids = list(await session.scalars(stmt))
    for shortage_id in ids:
        await shortages.cancel_shortage(
            session, user, shortage_id, "Reset by the e2e Scenario 1 setup."
        )
    return len(ids)


async def main() -> None:
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        await seed(session)
        for name, org_type, lat, lng in EXTRA_ORGS:
            await ensure_org(session, name, org_type, lat, lng)
        orgs = {o.name: o for o in await session.scalars(select(Organization))}
        product = await session.scalar(select(Product).where(Product.code == "SURG-KIT-A"))
        assert product is not None, "Surgical Kit A is missing from the catalog"

        cancelled = await cancel_open_shortages(session, orgs["Hospital A"], product)

        for name, (on_hand, reserved, allocated, safety, expiry_days) in BATCHES.items():
            org = orgs[name]
            facility = await session.scalar(select(Facility).where(Facility.org_id == org.id))
            assert facility is not None
            batch = await session.scalar(
                select(InventoryBatch).where(
                    InventoryBatch.facility_id == facility.id,
                    InventoryBatch.product_id == product.id,
                    InventoryBatch.batch_no == BATCH_NO,
                )
            )
            if batch is None:
                batch = InventoryBatch(
                    org_id=org.id,
                    facility_id=facility.id,
                    product_id=product.id,
                    batch_no=BATCH_NO,
                    unit_cost_paise=1500,
                )
                session.add(batch)
            batch.on_hand = on_hand
            batch.reserved = reserved
            batch.allocated = allocated
            batch.safety_stock = safety
            batch.quarantined = 0
            batch.expiry_date = now.date() + timedelta(days=expiry_days)
            batch.last_verified_at = now - timedelta(hours=1)

        for name, (price, lead, available) in OFFERS.items():
            org = orgs[name]
            offer = await session.scalar(
                select(SupplierOffer).where(
                    SupplierOffer.org_id == org.id, SupplierOffer.product_id == product.id
                )
            )
            if offer is None:
                offer = SupplierOffer(org_id=org.id, product_id=product.id)
                session.add(offer)
            offer.unit_price_paise = price
            offer.lead_time_hours = lead
            offer.available_qty = available
            offer.updated_at = now

        authorized = set(
            await session.scalars(
                select(ProductAuthorization.org_id).where(
                    ProductAuthorization.product_id == product.id
                )
            )
        )
        for name in AUTHORIZED:
            if orgs[name].id not in authorized:
                session.add(ProductAuthorization(org_id=orgs[name].id, product_id=product.id))
        await session.execute(  # Hospital E must fail the authorization gate
            delete(ProductAuthorization).where(
                ProductAuthorization.org_id == orgs["Hospital E"].id,
                ProductAuthorization.product_id == product.id,
            )
        )

        await session.commit()
    print(f"Scenario 1 seeded; cancelled {cancelled} earlier Hospital A shortage(s).")


if __name__ == "__main__":
    asyncio.run(main())
