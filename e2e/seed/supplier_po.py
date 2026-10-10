"""Setup for the supplier-web Playwright test (e2e/tests/supplier-po.spec.ts): a purchase order
SENT to Supplier Y, reached through the hub's own services as in Scenario 1 steps 4-5
(docs/specs/demo-scenarios.md), but for Nebulizer Masks, which the demo seed neither stocks nor
offers (`E2E_PRODUCTS` in app/seed.py), so Supplier Y is the only source and the scenarios'
data is never touched.

Run from services/hub-api, against the database the hub under test uses (the spec runs it):

    cd services/hub-api && uv run python ../../e2e/seed/supplier_po.py

Each run:
- loads the dev seed and adds Supplier Y with a supplier desk user if missing;
- gives Supplier Y a Nebulizer Mask offer and authorization if it has none (an existing
  offer is left as it is: `make demo-reset` starts again from scratch);
- cancels Hospital A's open Nebulizer Mask shortages from earlier runs (hub service, with
  audit rows);
- reports a new Nebulizer Mask shortage as Hospital A's store manager: matching recommends a
  BUY from Supplier Y, which Hospital A's approver approves, so the hub sends the PO;
- issues Supplier Y's desk user a session (no login, so the hub's 5-logins-per-minute budget
  stays with the other e2e tests).

Prints one JSON line: {"purchase_order_id", "access_token", "refresh_token"}.
"""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "hub-api"))

from app.auth import service as auth  # noqa: E402
from app.auth.models import Role, User  # noqa: E402
from app.catalog.models import Product, ProductAuthorization, SupplierOffer  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.orgs.models import Facility, Organization, OrgType  # noqa: E402
from app.purchase_orders.models import PurchaseOrder  # noqa: E402
from app.recommendations import service as recommendations  # noqa: E402
from app.recommendations.models import Recommendation  # noqa: E402
from app.seed import PASSWORD, seed  # noqa: E402
from app.shortages import service as shortages  # noqa: E402
from app.shortages.models import Priority, Shortage  # noqa: E402
from app.shortages.schemas import ShortageCreate  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

PRODUCT = "RSP-NEB-MSK"
CANCELLABLE = ("OPEN", "MATCHING", "AWAITING_DECISION")
DESK_EMAIL = "supplier.desk@supplier-y.demo"
# Supplier Y as in Scenario 1 (e2e/seed/scenario1.py); unit price in paise, hours, qty.
Y_LOCATION = (12.8500, 77.6600)
Y_OFFER = (4500, 12, 5000)


async def user(session: AsyncSession, email: str) -> User:
    found = await session.scalar(select(User).where(User.email == email))
    assert found is not None, f"{email} is missing; run `make seed` first"
    return found


async def ensure_supplier_y(session: AsyncSession) -> tuple[Organization, User]:
    y = await session.scalar(select(Organization).where(Organization.name == "Supplier Y"))
    if y is None:
        lat, lng = Y_LOCATION
        y = Organization(name="Supplier Y", type=OrgType.SUPPLIER, lat=lat, lng=lng)
        session.add(y)
        await session.flush()
    desk = await session.scalar(select(User).where(User.email == DESK_EMAIL))
    if desk is None:
        role = await session.scalar(select(Role).where(Role.name == "SUPPLIER_DESK"))
        assert role is not None
        desk = User(
            email=DESK_EMAIL,
            password_hash=auth.hash_password(PASSWORD),
            full_name="Supplier Y Supplier Desk",
            org_id=y.id,
            roles=[role],
        )
        session.add(desk)
        await session.flush()
    return y, desk


async def main() -> None:
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        await seed(session)
        y, desk = await ensure_supplier_y(session)
        product = await session.scalar(select(Product).where(Product.code == PRODUCT))
        assert product is not None, f"{PRODUCT} is missing from the catalog"

        offer = await session.scalar(
            select(SupplierOffer).where(
                SupplierOffer.org_id == y.id, SupplierOffer.product_id == product.id
            )
        )
        if offer is None:  # create-only: an existing offer changes only through the hub
            price, lead, qty = Y_OFFER
            session.add(
                SupplierOffer(
                    org_id=y.id,
                    product_id=product.id,
                    unit_price_paise=price,
                    lead_time_hours=lead,
                    available_qty=qty,
                    updated_at=now,
                )
            )
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
        stale = await session.scalars(
            select(Shortage.id).where(
                Shortage.org_id == manager.org_id,
                Shortage.product_id == product.id,
                Shortage.status.in_(CANCELLABLE),
            )
        )
        for shortage_id in list(stale):
            await shortages.cancel_shortage(
                session, manager, shortage_id, "Reset by the e2e supplier setup."
            )

        facility = await session.scalar(select(Facility).where(Facility.org_id == manager.org_id))
        assert facility is not None
        shortage = await shortages.create_shortage(
            session,
            manager,
            ShortageCreate(
                facility_id=facility.id,
                product_id=product.id,
                qty_required=200,
                qty_local_usable=0,
                required_by=now + timedelta(hours=72),
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
        po = await session.get(PurchaseOrder, po_id)
        assert po is not None and po.supplier_org_id == y.id, "the PO went to another supplier"
        tokens = await auth.issue_tokens(session, desk)
        await session.commit()
    print(
        json.dumps(
            {
                "purchase_order_id": str(po_id),
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
