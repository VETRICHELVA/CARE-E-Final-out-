"""Drive Scenario 1 (docs/specs/demo-scenarios.md) to the step a copilot eval case needs:
2 (TRANSFER from Hospital B requested), 4 (B declined without a reason; BUY from Supplier Y
pending) or 7 (bought, delivered, received 790 of 850; residual of 60 matching).

Runs in the HUB's environment, against the database the hub under test uses, through the
hub's own services (as e2e/seed/*.py do), and prints one JSON line for the eval runner:
{"step", "refs": {"shortage", "recommendation"?, "shipment"?}, "tokens": {email: token}}.

    cd services/hub-api && uv run python ../ai-service/evals/scenario1_steps.py --to 2
    cd services/hub-api && uv run python ../ai-service/evals/scenario1_steps.py --to 4 \
        --state /tmp/s1.json

`--to 2` resets Scenario 1 first (e2e/seed/scenario1.py: batches, offers, authorizations;
cancels Hospital A's open Surgical Kit A shortages). `--to 4` and `--to 7` continue the
shortage in `--state` (the previous step's output). Access tokens are issued directly, so the
hub's login rate limit is not spent.
"""

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "hub-api"))
sys.path.insert(0, str(ROOT / "e2e" / "seed"))

import scenario1  # noqa: E402  (e2e/seed/scenario1.py)
from app.auth import service as auth  # noqa: E402
from app.auth.models import User  # noqa: E402
from app.catalog.models import Product  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.domain.fulfillment import ShipmentStatus  # noqa: E402
from app.orgs.models import Facility  # noqa: E402
from app.purchase_orders import service as purchase_orders  # noqa: E402
from app.receiving import service as receiving  # noqa: E402
from app.receiving.schemas import ReceiptIn  # noqa: E402
from app.recommendations import service as recommendations  # noqa: E402
from app.recommendations.models import Recommendation  # noqa: E402
from app.shipments import service as shipments  # noqa: E402
from app.shipments.models import Driver, Shipment, Vehicle  # noqa: E402
from app.shortages import service as shortages  # noqa: E402
from app.shortages.models import Priority, Shortage  # noqa: E402
from app.shortages.schemas import ShortageCreate  # noqa: E402
from app.source_requests import service as source_requests  # noqa: E402
from app.source_requests.models import SourceRequest  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402
from supplier_po import ensure_supplier_y  # noqa: E402  (e2e/seed/supplier_po.py)

AS_USERS = ["approver@hospital-a.demo", "approver@hospital-b.demo"]


async def user(session: AsyncSession, email: str) -> User:
    found = await session.scalar(select(User).where(User.email == email))
    assert found is not None, f"{email} is missing; run `make seed` first"
    return found


async def step_2(session: AsyncSession, now: datetime) -> dict[str, Any]:
    """Step 1-2: Hospital A reports 1,000 required, 150 usable, CRITICAL, by now + 72 h."""
    manager = await user(session, "store.manager@hospital-a.demo")
    facility = await session.scalar(select(Facility).where(Facility.org_id == manager.org_id))
    product = await session.scalar(select(Product).where(Product.code == "SURG-KIT-A"))
    assert facility is not None and product is not None
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
        ),
        now=now,
    )
    requests = list(
        await session.scalars(select(SourceRequest).where(SourceRequest.shortage_id == shortage.id))
    )
    assert len(requests) == 1, f"expected one request to Hospital B, got {len(requests)}"
    return {"shortage": str(shortage.id)}


async def step_4(session: AsyncSession, refs: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Step 3-4: Hospital B declines without a reason; the hub recommends BUY from Y."""
    b = await user(session, "store.manager@hospital-b.demo")
    shortage_id = uuid.UUID(refs["shortage"])
    request = await session.scalar(
        select(SourceRequest).where(
            SourceRequest.shortage_id == shortage_id, SourceRequest.status == "REQUESTED"
        )
    )
    assert request is not None, "Hospital B has no open request; run --to 2 first"
    await source_requests.decline(session, b, request.id, None, now=now)
    rec = await session.scalar(
        select(Recommendation).where(
            Recommendation.shortage_id == shortage_id, Recommendation.status == "PENDING"
        )
    )
    assert rec is not None and rec.type == "BUY", f"expected a BUY, got {rec and rec.type}"
    return {**refs, "recommendation": str(rec.id)}


async def step_7(session: AsyncSession, refs: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Steps 5-7: approve the purchase; Supplier Y acknowledges and dispatches; SwiftMed's
    Ravi delivers; Hospital A receives 790 and accepts 790."""
    approver = await user(session, "approver@hospital-a.demo")
    _, desk = await ensure_supplier_y(session)
    _, _, po_id = await recommendations.approve(
        session, approver, uuid.UUID(refs["recommendation"]), None, now=now
    )
    assert po_id is not None
    await purchase_orders.acknowledge(session, desk, po_id, None)
    await purchase_orders.dispatch(session, desk, po_id, None)
    shipment = await session.scalar(select(Shipment).where(Shipment.purchase_order_id == po_id))
    assert shipment is not None

    dispatcher = await user(session, "dispatcher@swiftmed.demo")
    ravi = await user(session, "driver@swiftmed.demo")
    driver = await session.scalar(select(Driver).where(Driver.user_id == ravi.id))
    van = await session.scalar(
        select(Vehicle).where(
            Vehicle.org_id == dispatcher.org_id, Vehicle.has_cold_chain.is_(False)
        )
    )
    assert driver is not None and van is not None, "SwiftMed's fleet is missing; make seed"
    await shipments.assign(
        session, dispatcher, shipment.id, driver_id=driver.id, vehicle_id=van.id, reason=None
    )
    for status in (ShipmentStatus.PICKED_UP, ShipmentStatus.IN_TRANSIT, ShipmentStatus.DELIVERED):
        await shipments.move(session, ravi, shipment.id, status, reason=None, now=now)

    receiver = await user(session, "receiver@hospital-a.demo")
    await receiving.record(
        session,
        receiver,
        shipment.id,
        ReceiptIn(
            received=790,
            accepted=790,
            rejected=0,
            condition="GOOD",
            expiry_date=date.today() + timedelta(days=365),
        ),
        now=now,
    )
    parent = await session.get_one(Shortage, uuid.UUID(refs["shortage"]), populate_existing=True)
    assert parent.status == "PARTIALLY_RESOLVED", parent.status
    return {**refs, "shipment": str(shipment.id)}


async def main(to: int, previous: dict[str, Any]) -> None:
    now = datetime.now(UTC)
    if to == 2:
        await scenario1.main()  # reset Scenario 1's seed state (prints its own line)
    async with SessionLocal() as session:
        if to == 2:
            refs = await step_2(session, now)
        elif to == 4:
            refs = await step_4(session, previous["refs"], now)
        else:
            refs = await step_7(session, previous["refs"], now)
        tokens = {}
        for email in AS_USERS:
            pair = await auth.issue_tokens(session, await user(session, email))
            tokens[email] = pair.access_token
        await session.commit()
    print(json.dumps({"step": to, "refs": refs, "tokens": tokens}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--to", type=int, choices=(2, 4, 7), required=True)
    parser.add_argument("--state", type=Path, help="the previous step's JSON line")
    args = parser.parse_args()
    if args.to != 2 and args.state is None:
        parser.error("--to 4 and --to 7 continue from --state")
    asyncio.run(main(args.to, json.loads(args.state.read_text()) if args.state else {}))
