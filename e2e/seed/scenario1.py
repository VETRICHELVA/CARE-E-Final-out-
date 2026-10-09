"""Scenario 1 reset for the copilot eval runner (services/ai-service/evals/scenario1_steps.py,
docs/specs/demo-scenarios.md). `make e2e` resets all three scenarios with e2e/seed/scenarios.py
instead.

Run from services/hub-api, against the database the hub under test uses:

    cd services/hub-api && uv run python ../../e2e/seed/scenario1.py

Idempotent, and it resets Scenario 1 each time:
- runs the demo seed (`app.seed.seed`, S20), which puts Scenario 1's batches, offers and
  authorizations back to the spec's numbers with times relative to now (Hospital D's expiry
  pinned so the shelf-life gate reads "Expires in 12 days" now);
- empties the `E2E-S1` Surgical Kit A batches that this script wrote before S20, which would
  otherwise add to the seeded stock;
- cancels Hospital A's open Surgical Kit A shortages from earlier runs through the hub's own
  service, which supersedes their source requests and releases their holds (with audit rows).
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "hub-api"))

from app.auth.models import User  # noqa: E402
from app.catalog.models import Product  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.inventory.models import InventoryBatch  # noqa: E402
from app.orgs.models import Organization  # noqa: E402
from app.seed import seed  # noqa: E402
from app.shortages import service as shortages  # noqa: E402
from app.shortages.models import Shortage  # noqa: E402
from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

LEGACY_BATCH_NO = "E2E-S1"
CANCELLABLE = ("OPEN", "MATCHING", "AWAITING_DECISION")


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
    async with SessionLocal() as session:
        await seed(session)
        a = await session.scalar(select(Organization).where(Organization.name == "Hospital A"))
        product = await session.scalar(select(Product).where(Product.code == "SURG-KIT-A"))
        assert a is not None and product is not None
        cancelled = await cancel_open_shortages(session, a, product)
        await session.execute(
            update(InventoryBatch)
            .where(InventoryBatch.batch_no == LEGACY_BATCH_NO)
            .values(on_hand=0, reserved=0, allocated=0, safety_stock=0, quarantined=0)
        )
        await session.commit()
    print(f"Scenario 1 seeded; cancelled {cancelled} earlier Hospital A shortage(s).")


if __name__ == "__main__":
    asyncio.run(main())
