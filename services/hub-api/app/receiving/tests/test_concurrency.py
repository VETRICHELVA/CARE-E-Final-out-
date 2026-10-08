"""Receipts racing on a database whose transactions really commit: two receivers recording
the two shipments of one shortage at once (exactly one reconciles it), and the same
shipment twice (one 201, one 409). Each test makes its own orgs, product and records."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import service as auth_service
from app.auth.models import User
from app.catalog.models import Product
from app.db import get_session
from app.events.models import EventOutbox
from app.main import create_app
from app.orgs.models import OrgType
from app.purchase_orders.models import PurchaseOrder
from app.receiving.models import Receipt, Reconciliation
from app.receiving.tests.conftest import receipt
from app.shipments.models import Driver, Shipment, Vehicle
from app.shortages.models import Shortage
from app.source_requests.tests import conftest as s06
from app.source_requests.tests.conftest import add_org, add_user, facility_of, unique

pytestmark = pytest.mark.anyio
Maker = async_sessionmaker[AsyncSession]

committed = s06.committed
committed_db_url = s06.committed_db_url


async def setup(sm: Maker, qtys: list[int]) -> tuple[Shortage, list[Shipment], list[User]]:
    """A shortage IN_FULFILLMENT with one DELIVERED purchase-order shipment per qty, and two
    receivers of the buying hospital."""
    tag = unique()
    now = datetime.now(UTC)
    async with sm() as session:
        product = Product(
            code=f"RC-{tag}",
            name=f"Receipt race kit {tag}",
            category="Test",
            unit="each",
            default_min_shelf_life_days=30,
        )
        session.add(product)
        hospital = await add_org(session, f"Hospital {tag}", OrgType.HOSPITAL, 12.97, 77.59)
        requester = await add_user(session, hospital, "REQUESTER", f"req-{tag}@a.test")
        receivers = [
            await add_user(session, hospital, "RECEIVER", f"rcv{n}-{tag}@a.test") for n in (1, 2)
        ]
        fleet = await add_org(session, f"Fleet {tag}", OrgType.LOGISTICS, 12.98, 77.64)
        driver_user = await add_user(session, fleet, "DRIVER", f"drv-{tag}@l.test")
        driver = Driver(org_id=fleet.id, user_id=driver_user.id, phone="+91 90000 00000")
        vehicle = Vehicle(org_id=fleet.id, reg_no=f"V-{tag}", has_cold_chain=False)
        session.add_all([driver, vehicle])
        await session.flush()
        shortage = Shortage(
            org_id=hospital.id,
            facility_id=(await facility_of(session, hospital)).id,
            product_id=product.id,
            qty_required=sum(qtys),
            qty_local_usable=0,
            shortfall=sum(qtys),
            required_by=now + timedelta(hours=72),
            priority="ROUTINE",
            min_shelf_life_days=30,
            status="IN_FULFILLMENT",
            created_by=requester.id,
            source="FORM",
        )
        session.add(shortage)
        await session.flush()
        shipments = []
        for n, qty in enumerate(qtys):
            supplier = await add_org(session, f"Supplier {n} {tag}", OrgType.SUPPLIER, 12.9, 77.6)
            po = PurchaseOrder(
                id=uuid.uuid4(),
                shortage_id=shortage.id,
                supplier_org_id=supplier.id,
                product_id=product.id,
                qty=qty,
                unit_price_paise=1000,
                status="DISPATCHED",
                eta=now,
            )
            session.add(po)
            await session.flush()
            shipment = Shipment(
                shortage_id=shortage.id,
                purchase_order_id=po.id,
                from_org_id=supplier.id,
                to_org_id=hospital.id,
                carrier_org_id=fleet.id,
                product_id=product.id,
                qty=qty,
                requires_cold_chain=False,
                status="DELIVERED",
                driver_id=driver.id,
                vehicle_id=vehicle.id,
                status_history=[],
            )
            session.add(shipment)
            shipments.append(shipment)
        await session.commit()
    return shortage, shipments, receivers


@pytest.fixture
async def client(committed: Maker) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def session_per_request() -> AsyncIterator[AsyncSession]:
        async with committed() as s:
            yield s

    app.dependency_overrides[get_session] = session_per_request
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test/api/v1"
    ) as c:
        yield c


async def token(sm: Maker, user: User) -> dict[str, str]:
    async with sm() as session:
        pair = await auth_service.issue_tokens(session, await session.get_one(User, user.id))
        await session.commit()
    return {"Authorization": f"Bearer {pair.access_token}"}


async def count(session: AsyncSession, model: type, *where: ColumnElement[bool]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def test_two_receipts_of_a_split_at_once_reconcile_exactly_once(
    committed: Maker, client: httpx.AsyncClient
) -> None:
    shortage, (first, second), receivers = await setup(committed, [60, 40])
    one, two = [await token(committed, u) for u in receivers]
    a, b = await asyncio.gather(
        client.post(
            f"/shipments/{first.id}/receipt", json=receipt(60, 60, batch_no="R-1"), headers=one
        ),
        client.post(
            f"/shipments/{second.id}/receipt", json=receipt(40, 30, 10, batch_no="R-2"), headers=two
        ),
    )
    assert (a.status_code, b.status_code) == (201, 201), (a.text, b.text)
    # Exactly one of the two saw every receipt, so exactly one carries the reconciliation.
    assert sum(r.json()["reconciliation"] is not None for r in (a, b)) == 1
    async with committed() as session:
        done = await session.get_one(Shortage, shortage.id)
        assert done.status == "PARTIALLY_RESOLVED"
        assert await count(session, Reconciliation, Reconciliation.shortage_id == shortage.id) == 2
        assert await count(session, Shortage, Shortage.parent_shortage_id == shortage.id) == 1
        residual = await session.scalar(
            select(Shortage).where(Shortage.parent_shortage_id == shortage.id)
        )
        assert residual is not None and residual.shortfall == 10
        completed = await count(
            session,
            EventOutbox,
            EventOutbox.event_type == "reconciliation.completed",
            EventOutbox.payload["data"]["shortage_id"].astext == str(shortage.id),
        )
        assert completed == 1


async def test_the_same_shipment_twice_at_once_records_one_receipt(
    committed: Maker, client: httpx.AsyncClient
) -> None:
    shortage, (shipment,), receivers = await setup(committed, [50])
    one, two = [await token(committed, u) for u in receivers]
    a, b = await asyncio.gather(
        client.post(
            f"/shipments/{shipment.id}/receipt", json=receipt(50, 50, batch_no="X-1"), headers=one
        ),
        client.post(
            f"/shipments/{shipment.id}/receipt", json=receipt(50, 50, batch_no="X-2"), headers=two
        ),
    )
    assert sorted([a.status_code, b.status_code]) == [201, 409]
    loser = a if a.status_code == 409 else b
    assert loser.json()["code"] == "invalid_transition"
    async with committed() as session:
        assert await count(session, Receipt, Receipt.shipment_id == shipment.id) == 1
        done = await session.get_one(Shortage, shortage.id)
        assert done.status == "RESOLVED"
