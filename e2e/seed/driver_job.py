"""Setup for the delivery-web Playwright test (e2e/tests/driver-jobs.spec.ts): an unassigned
shipment on SwiftMed Logistics' dispatch board, reached through the hub's own services as in
Scenario 1 steps 4-6 (docs/specs/demo-scenarios.md), but for Face Shields so it never touches
the other e2e tests' products (Surgical Kit A, Nebulizer Mask).

Run from services/hub-api, against the database the hub under test uses (the spec runs it):

    cd services/hub-api && uv run python ../../e2e/seed/driver_job.py

Each run:
- loads the dev seed (with SwiftMed's drivers Ravi and Priya and its two vans) and adds
  Supplier Y with a supplier desk user if missing;
- gives Supplier Y a fresh Face Shield offer and authorization;
- cancels Hospital A's open Face Shield shortages from earlier runs (hub service, with audit
  rows);
- reports a new Face Shield shortage as Hospital A's store manager: matching recommends a BUY
  from Supplier Y, which Hospital A's approver approves; Supplier Y's desk acknowledges and
  dispatches the purchase order, which creates the shipment (CREATED, no carrier yet);
- issues sessions for SwiftMed's dispatcher and for Ravi, the driver (no login, so the hub's
  5-logins-per-minute budget stays with the other e2e tests).

Prints one JSON line: {"shipment_id", "dispatcher": {access_token, refresh_token},
"driver": {access_token, refresh_token}}.
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
from app.purchase_orders import service as purchase_orders  # noqa: E402
from app.recommendations import service as recommendations  # noqa: E402
from app.recommendations.models import Recommendation  # noqa: E402
from app.seed import seed  # noqa: E402
from app.shipments.models import Shipment  # noqa: E402
from app.shortages import service as shortages  # noqa: E402
from app.shortages.models import Priority, Shortage  # noqa: E402
from app.shortages.schemas import ShortageCreate  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402
from supplier_po import DESK_EMAIL, ensure_supplier_y  # noqa: E402

PRODUCT = "PPE-FSH"
CANCELLABLE = ("OPEN", "MATCHING", "AWAITING_DECISION")
# unit price in paise, lead time in hours, available qty
Y_OFFER = (6500, 6, 3000)
DISPATCHER_EMAIL = "dispatcher@swiftmed.demo"
DRIVER_EMAIL = "driver@swiftmed.demo"  # Ravi (app/seed.py, DRIVERS)


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
        assert not product.requires_cold_chain, "the driver test uses an ordinary product"

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
        approver = await user(session, "approver@hospital-a.demo")
        desk = await user(session, DESK_EMAIL)
        stale = await session.scalars(
            select(Shortage.id).where(
                Shortage.org_id == manager.org_id,
                Shortage.product_id == product.id,
                Shortage.status.in_(CANCELLABLE),
            )
        )
        for shortage_id in list(stale):
            await shortages.cancel_shortage(
                session, manager, shortage_id, "Reset by the e2e driver setup."
            )

        facility = await session.scalar(select(Facility).where(Facility.org_id == manager.org_id))
        assert facility is not None
        shortage = await shortages.create_shortage(
            session,
            manager,
            ShortageCreate(
                facility_id=facility.id,
                product_id=product.id,
                qty_required=120,
                qty_local_usable=0,
                required_by=now + timedelta(hours=48),
                priority=Priority.ROUTINE,
            ),
            now=now,
        )
        rec = await session.scalar(
            select(Recommendation).where(
                Recommendation.shortage_id == shortage.id, Recommendation.status == "PENDING"
            )
        )
        assert rec is not None and rec.type == "BUY", (
            f"expected a BUY recommendation, got {rec and rec.type}; "
            f"another source of {PRODUCT} is eligible in this database"
        )
        _, _, po_id = await recommendations.approve(session, approver, rec.id, None, now=now)
        assert po_id is not None
        await purchase_orders.acknowledge(session, desk, po_id, None)
        await purchase_orders.dispatch(session, desk, po_id, None)
        shipment = await session.scalar(select(Shipment).where(Shipment.purchase_order_id == po_id))
        assert shipment is not None and shipment.status == "CREATED", "no shipment was created"

        dispatcher = await tokens(session, await user(session, DISPATCHER_EMAIL))
        driver = await tokens(session, await user(session, DRIVER_EMAIL))
        await session.commit()
    print(json.dumps({"shipment_id": str(shipment.id), "dispatcher": dispatcher, "driver": driver}))


if __name__ == "__main__":
    asyncio.run(main())
