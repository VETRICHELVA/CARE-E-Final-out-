"""Setup for the Scenario 1 receipt Playwright test (e2e/tests/scenario1-receipt.spec.ts): a
shortage at Hospital A AWAITING_DECISION on a BUY from Supplier Y, as at Scenario 1 step 4
(docs/specs/demo-scenarios.md), with Scenario 1's figures (1,000 required, 150 usable, shortfall
850) but for Sterile Drapes, so it never touches the other e2e tests' products (Surgical Kit A,
Nebulizer Mask, Face Shield).

Run from services/hub-api, against the database the hub under test uses (the spec runs it):

    cd services/hub-api && uv run python ../../e2e/seed/scenario1_receipt.py

Each run:
- loads the dev seed (SwiftMed's drivers and vans) and adds Supplier Y with a supplier desk
  user if missing;
- gives Supplier Y a fresh Sterile Drape offer and authorization (the only source of it);
- cancels Hospital A's open Sterile Drape shortages from earlier runs (hub service, with audit
  rows);
- reports the shortage as Hospital A's store manager: matching recommends a BUY from
  Supplier Y, which the test approves in hospital-web;
- issues sessions (no login, so the hub's 5-logins-per-minute budget stays with the other e2e
  tests) for Hospital A's approver and receiver, Supplier Y's desk, SwiftMed's dispatcher and
  Priya, the second driver (Ravi drives the delivery-web test's job).

Prints one JSON line: {"shortage_id", "approver", "receiver", "supplier", "dispatcher",
"driver"}, each session as {access_token, refresh_token}.
"""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "hub-api"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.auth import service as auth  # noqa: E402
from app.auth.models import User  # noqa: E402
from app.catalog.models import Product, ProductAuthorization, SupplierOffer  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.orgs.models import Facility  # noqa: E402
from app.recommendations.models import Recommendation  # noqa: E402
from app.seed import seed  # noqa: E402
from app.shortages import service as shortages  # noqa: E402
from app.shortages.models import Priority, Shortage  # noqa: E402
from app.shortages.schemas import ShortageCreate  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402
from supplier_po import DESK_EMAIL, ensure_supplier_y  # noqa: E402

PRODUCT = "SURG-DRP-STR"
CANCELLABLE = ("OPEN", "MATCHING", "AWAITING_DECISION")
# Supplier Y's Scenario 1 offer: unit price in paise, lead time in hours, available qty
Y_OFFER = (2800, 22, 2000)
DISPATCHER_EMAIL = "dispatcher@swiftmed.demo"
DRIVER_EMAIL = "driver2@swiftmed.demo"  # Priya (app/seed.py, DRIVERS)


async def user(session: AsyncSession, email: str) -> User:
    found = await session.scalar(select(User).where(User.email == email))
    assert found is not None, f"{email} is missing; run `make seed` first"
    return found


async def tokens(session: AsyncSession, who: User) -> dict[str, str]:
    pair = await auth.issue_tokens(session, who)
    return {"access_token": pair.access_token, "refresh_token": pair.refresh_token}


async def main() -> None:
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        await seed(session)
        y, _ = await ensure_supplier_y(session)
        product = await session.scalar(select(Product).where(Product.code == PRODUCT))
        assert product is not None, f"{PRODUCT} is missing from the catalog"
        assert not product.requires_cold_chain, "the receipt test uses an ordinary product"

        offer = await session.scalar(
            select(SupplierOffer).where(
                SupplierOffer.org_id == y.id, SupplierOffer.product_id == product.id
            )
        )
        if offer is None:
            offer = SupplierOffer(org_id=y.id, product_id=product.id)
            session.add(offer)
        offer.unit_price_paise, offer.lead_time_hours, offer.available_qty = Y_OFFER
        offer.updated_at = now
        authorized = await session.scalar(
            select(ProductAuthorization).where(
                ProductAuthorization.org_id == y.id, ProductAuthorization.product_id == product.id
            )
        )
        if authorized is None:
            session.add(ProductAuthorization(org_id=y.id, product_id=product.id))
        await session.flush()

        manager = await user(session, "store.manager@hospital-a.demo")
        stale = await session.scalars(
            select(Shortage.id).where(
                Shortage.org_id == manager.org_id,
                Shortage.product_id == product.id,
                Shortage.status.in_(CANCELLABLE),
            )
        )
        for shortage_id in list(stale):
            await shortages.cancel_shortage(
                session, manager, shortage_id, "Reset by the e2e receipt setup."
            )

        facility = await session.scalar(select(Facility).where(Facility.org_id == manager.org_id))
        assert facility is not None
        shortage = await shortages.create_shortage(
            session,
            manager,
            ShortageCreate(
                facility_id=facility.id,
                product_id=product.id,
                qty_required=1000,
                qty_local_usable=150,
                required_by=now + timedelta(hours=72),
                priority=Priority.CRITICAL,
                min_shelf_life_days=30,
            ),
            now=now,
        )
        assert shortage.shortfall == 850, f"shortfall {shortage.shortfall}, expected 850"
        rec = await session.scalar(
            select(Recommendation).where(
                Recommendation.shortage_id == shortage.id, Recommendation.status == "PENDING"
            )
        )
        assert rec is not None and rec.type == "BUY", (
            f"expected a BUY recommendation, got {rec and rec.type}; "
            f"another source of {PRODUCT} is eligible in this database"
        )

        out = {
            "shortage_id": str(shortage.id),
            "approver": await tokens(session, await user(session, "approver@hospital-a.demo")),
            "receiver": await tokens(session, await user(session, "receiver@hospital-a.demo")),
            "supplier": await tokens(session, await user(session, DESK_EMAIL)),
            "dispatcher": await tokens(session, await user(session, DISPATCHER_EMAIL)),
            "driver": await tokens(session, await user(session, DRIVER_EMAIL)),
        }
        await session.commit()
    print(json.dumps(out))


if __name__ == "__main__":
    asyncio.run(main())
