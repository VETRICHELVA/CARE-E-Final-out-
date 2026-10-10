"""Scenario 1 reset for the copilot eval runner (services/ai-service/evals/scenario1_steps.py,
docs/specs/demo-scenarios.md). `make e2e` resets all three scenarios with e2e/seed/scenarios.py
instead.

Run from services/hub-api, against the database the hub under test uses:

    cd services/hub-api && uv run python ../../e2e/seed/scenario1.py

Each run:
- runs the demo seed (`app.seed.seed`, S20), which adds Scenario 1's batches, offers and
  authorizations if they are missing; it never changes existing stock (CLAUDE.md rule 5), so
  start from `make demo-reset` for the spec's numbers and times relative to now (the batches'
  "verified" times and the offers age; Hospital D's expiry reads "Expires in 12 days" on the
  day it was seeded);
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
from app.orgs.models import Organization  # noqa: E402
from app.seed import seed  # noqa: E402
from app.shortages import service as shortages  # noqa: E402
from app.shortages.models import Shortage  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

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
        await session.commit()
    print(f"Scenario 1 seeded; cancelled {cancelled} earlier Hospital A shortage(s).")


if __name__ == "__main__":
    asyncio.run(main())
