"""Reset the three demo scenarios (docs/specs/demo-scenarios.md) for `make e2e`, on top of the
full demo seed, so the suite can run again and again on the same database (S20's Verify runs
`make e2e` three times in a row after one `make demo-reset`).

Run from services/hub-api, against the database the hub under test uses (`make e2e` does):

    cd services/hub-api && uv run python ../../e2e/seed/scenarios.py

Needs `make seed` (or `make demo-reset`) first. Each run:
- runs the demo seed (`app.seed.seed`): Scenario 1-3 batches, offers and authorizations back
  to the spec's numbers, times relative to now;
- cancels, through the hub's own service (audited), the scenario shortages earlier runs left
  open: Hospital A's Surgical Kit A, Hospital C's Rapid Diagnostic Kit and Hospital E's
  IV Cannula 20G (supersedes their requests and releases their holds);
- withdraws Hospital B's live IV Cannula 20G surplus posts (audited), so B's batch can be
  offered again;
- empties the stock earlier runs received at Hospital E (IV Cannula 20G) and Hospital C (Rapid
  Diagnostic Kit), which would otherwise move E's predicted stock-out away from 4 days, and the
  `E2E-S1` batches of the pre-S20 Scenario 1 setup;
- takes `cb-01` off any shipment (audited), so the Scenario 2 test can attach it;
- re-runs the forecast for Hospitals B and E (statsmodels, in the hub).
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "hub-api"))

from app.auth.models import User  # noqa: E402
from app.catalog.models import Product  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.forecasting import service as forecasting  # noqa: E402
from app.inventory.models import InventoryBatch  # noqa: E402
from app.iot import service as iot  # noqa: E402
from app.iot.models import Device  # noqa: E402
from app.orgs.models import Organization  # noqa: E402
from app.seed import SEED_BATCH, seed  # noqa: E402
from app.shortages import service as shortages  # noqa: E402
from app.shortages.models import Shortage  # noqa: E402
from app.surplus import service as surplus  # noqa: E402
from app.surplus.models import SurplusPost  # noqa: E402
from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

REASON = "Reset by the e2e scenario setup."
CANCELLABLE = ("OPEN", "MATCHING", "AWAITING_DECISION")
LIVE_POSTS = ("OPEN", "MATCHED")
LEGACY_BATCH_NO = "E2E-S1"
# (requesting hospital, product code): each scenario's shortage
SHORTAGES = [("Hospital A", "SURG-KIT-A"), ("Hospital C", "DIAG-RDK"), ("Hospital E", "IV-CAN-20G")]
# (hospital, product code) whose received (non-seed) stock a scenario's numbers exclude
RECEIVED = [("Hospital E", "IV-CAN-20G"), ("Hospital C", "DIAG-RDK")]
DEVICE = "cb-01"


async def user(session: AsyncSession, email: str) -> User:
    found = await session.scalar(select(User).where(User.email == email))
    assert found is not None, f"{email} is missing; run `make seed` first"
    return found


def domain(org: str) -> str:
    return org.lower().replace(" ", "-") + ".demo"


async def main() -> None:
    async with SessionLocal() as session:
        await seed(session)
        orgs = {o.name: o for o in await session.scalars(select(Organization))}
        products = {p.code: p for p in await session.scalars(select(Product))}

        cancelled = 0
        for org_name, code in SHORTAGES:
            manager = await user(session, f"store.manager@{domain(org_name)}")
            ids = list(
                await session.scalars(
                    select(Shortage.id).where(
                        Shortage.org_id == orgs[org_name].id,
                        Shortage.product_id == products[code].id,
                        Shortage.status.in_(CANCELLABLE),
                    )
                )
            )
            for shortage_id in ids:
                await shortages.cancel_shortage(session, manager, shortage_id, REASON)
            cancelled += len(ids)

        b_manager = await user(session, "store.manager@hospital-b.demo")
        posts = list(
            await session.scalars(
                select(SurplusPost.id).where(
                    SurplusPost.org_id == orgs["Hospital B"].id,
                    SurplusPost.product_id == products["IV-CAN-20G"].id,
                    SurplusPost.status.in_(LIVE_POSTS),
                )
            )
        )
        for post_id in posts:
            await surplus.withdraw(session, b_manager, post_id, REASON)

        for org_name, code in RECEIVED:
            await session.execute(
                update(InventoryBatch)
                .where(
                    InventoryBatch.org_id == orgs[org_name].id,
                    InventoryBatch.product_id == products[code].id,
                    InventoryBatch.batch_no != SEED_BATCH,
                )
                .values(on_hand=0, reserved=0, allocated=0, safety_stock=0, quarantined=0)
            )
        await session.execute(
            update(InventoryBatch)
            .where(InventoryBatch.batch_no == LEGACY_BATCH_NO)
            .values(on_hand=0, reserved=0, allocated=0, safety_stock=0, quarantined=0)
        )

        device = await session.scalar(select(Device).where(Device.device_id == DEVICE))
        assert device is not None, f"{DEVICE} is missing; run `make seed` first"
        if device.assigned_shipment_id is not None:
            dispatcher = await user(session, "dispatcher@swiftmed.demo")
            await iot.assign_device(session, dispatcher, device.id, None, REASON)
        await session.commit()

        summary = await forecasting.run(
            session, [orgs["Hospital B"].id, orgs["Hospital E"].id], forecasting.today()
        )
    print(
        f"Scenarios reset: cancelled {cancelled} shortage(s), withdrew {len(posts)} surplus "
        f"post(s), forecast {summary.series} series for Hospitals B and E."
    )


if __name__ == "__main__":
    asyncio.run(main())
