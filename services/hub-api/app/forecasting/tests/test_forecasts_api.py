"""GET /forecasts and POST /forecasts/run (S18): own org only, who may run, the synthetic
label, reorder suggestions and reproducible stored runs. Short histories keep these on the
fast moving average; test_scenario_3 covers Holt-Winters."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Product, SupplierOffer
from app.conftest import ClientFor, World
from app.forecasting import service
from app.forecasting.models import ConsumptionRecord, Forecast
from app.orgs.models import Organization
from app.surplus.tests.conftest import add_batch, today

pytestmark = pytest.mark.anyio


def history(
    session: AsyncSession, org: Organization, product: Product, per_day: int, synthetic: bool
) -> None:
    day = today()
    session.add_all(
        ConsumptionRecord(
            org_id=org.id,
            product_id=product.id,
            date=day - timedelta(days=i),
            qty=per_day,
            synthetic=synthetic,
        )
        for i in range(1, 31)
    )


@pytest.fixture
async def histories(session: AsyncSession, world: World, products: dict[str, Product]) -> None:
    history(session, world.hospital_a, products["IV-CAN-20G"], 30, synthetic=True)
    history(session, world.hospital_b, products["SURG-KIT-A"], 10, synthetic=False)
    await session.flush()


async def test_run_permissions(
    session: AsyncSession, world: World, client_for: ClientFor, histories: None
) -> None:
    a, b = world.hospital_a, world.hospital_b
    assert (await (await client_for()).post("/forecasts/run")).status_code == 401
    requester = await client_for(world.users["a.REQUESTER"])  # no inventory.edit
    assert (await requester.post("/forecasts/run")).status_code == 403
    supplier = await client_for(world.users["s.ADMIN"])  # has inventory.edit, not a hospital
    assert (await supplier.post("/forecasts/run")).status_code == 403
    manager = await client_for(world.users["b.STORE_MANAGER"])
    r = await manager.post("/forecasts/run", params={"org_id": str(a.id)})
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")  # another org

    r = await manager.post("/forecasts/run")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "org_ids": [str(b.id)],
        "series": 1,
        "models": {"moving-average-28/1": 1},
    }
    assert await session.scalar(select(Forecast).where(Forecast.org_id == a.id)) is None

    admin = await client_for(world.users["p.ADMIN"])  # platform admin: every hospital
    r = await admin.post("/forecasts/run")
    assert r.status_code == 200
    assert {str(a.id), str(b.id)} <= set(r.json()["org_ids"])
    r = await admin.post("/forecasts/run", params={"org_id": str(world.supplier.id)})
    assert r.status_code == 400
    r = await admin.post("/forecasts/run", params={"org_id": str(a.id)})
    assert (r.status_code, r.json()["org_ids"]) == (200, [str(a.id)])


async def test_forecasts_are_own_org_only_and_label_synthetic_history(
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    client_for: ClientFor,
    histories: None,
) -> None:
    iv, ska = products["IV-CAN-20G"], products["SURG-KIT-A"]
    await service.run(session, [world.hospital_a.id, world.hospital_b.id], today())
    a = await client_for(world.users["a.APPROVER"])  # any user of the org may read
    (item,) = (await a.get("/forecasts")).json()["items"]
    assert (item["product_id"], item["synthetic_history"]) == (str(iv.id), True)
    assert item["model_version"] == "moving-average-28/1"
    assert [d["predicted_qty"] for d in item["days"]] == [30.0] * 30
    # Another org's product: nothing (B's Surgical Kit A forecast is B's alone).
    assert (await a.get("/forecasts", params={"product_id": str(ska.id)})).json()["items"] == []
    b = await client_for(world.users["b.STORE_MANAGER"])
    (item,) = (await b.get("/forecasts")).json()["items"]
    assert (item["product_id"], item["synthetic_history"]) == (str(ska.id), False)


async def test_stockout_and_reorder_suggestion(
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    client_for: ClientFor,
    histories: None,
) -> None:
    iv = products["IV-CAN-20G"]
    await add_batch(session, world.hospital_a, iv, on_hand=200, safety_stock=50, expiry_days=200)
    session.add(
        SupplierOffer(
            org_id=world.supplier.id,
            product_id=iv.id,
            unit_price_paise=500,
            lead_time_hours=50,  # 3 days, rounded up
            available_qty=9000,
        )
    )
    await service.run(session, [world.hospital_a.id], today())
    a = await client_for(world.users["a.STORE_MANAGER"])
    (item,) = (await a.get("/forecasts")).json()["items"]
    # 200 usable at 30 a day: 180 after 6 days, 210 > 200 on the 7th day (today + 6).
    assert (item["usable_stock"], item["safety_stock"]) == (200, 50)
    assert item["stockout_date"] == str(today() + timedelta(days=6))
    # 3 days x 30 + safety 50 - usable 200 = -60 -> 0.
    assert item["reorder"] == {"lead_time_days": 3, "qty": 0}
    assert item["expiry_risks"] == []


async def test_a_stored_run_is_reproducible(
    session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    iv = products["IV-CAN-20G"]
    day = today()
    session.add_all(
        ConsumptionRecord(
            org_id=world.hospital_a.id,
            product_id=iv.id,
            date=day - timedelta(days=i),
            qty=30 if (day - timedelta(days=i)).weekday() < 5 else 25,
            synthetic=True,
        )
        for i in range(1, 91)
    )
    await session.flush()

    async def stored() -> list[tuple[object, ...]]:
        rows = await session.scalars(
            select(Forecast).where(Forecast.org_id == world.hospital_a.id).order_by(Forecast.date)
        )
        return [(f.date, f.predicted_qty, f.lower, f.upper, f.model_version) for f in rows]

    await service.run(session, [world.hospital_a.id], day)
    first = await stored()
    await service.run(session, [world.hospital_a.id], day)
    assert await stored() == first
    assert len(first) == 30 and first[0][4] == "holt-winters-weekly/1"
