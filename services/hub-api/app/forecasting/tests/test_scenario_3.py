"""Scenario 3 (demo-scenarios.md): expiry surplus meets a forecast stock-out. Real synthetic
history from scripts/seed/consumption.py, real statsmodels fits, the real clock."""

import runpy
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Product, ProductAuthorization
from app.conftest import ClientFor, World
from app.events import service as events
from app.forecasting import service
from app.forecasting.models import ConsumptionRecord
from app.orgs.models import Organization
from app.shortages.models import Candidate, MatchRun
from app.surplus.tests.conftest import add_batch, add_hospital, facility_of, today

pytestmark = pytest.mark.anyio

SEED_DIR = Path(__file__).resolve().parents[5] / "scripts" / "seed"


async def load_series(
    session: AsyncSession, org: Organization, name: str, product: Product
) -> None:
    generator = runpy.run_path(str(SEED_DIR / "consumption.py"))
    day = today()
    session.add_all(
        ConsumptionRecord(org_id=org.id, product_id=product.id, date=d, qty=q, synthetic=True)
        for d, q in zip(
            generator["history_days"](day),
            generator["series"](name, product.code, day),
            strict=True,
        )
    )
    await session.flush()


async def test_scenario_3(
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    client_for: ClientFor,
    redis: Redis,
) -> None:
    iv = products["IV-CAN-20G"]
    b = world.hospital_b
    e, e_users = await add_hospital(
        session, "Hospital E", 12.99, 77.66, roles=("STORE_MANAGER", "REQUESTER")
    )
    # An equal-cost source: same place as B, same unit cost, not near expiry, and an id that
    # sorts before B's, so only the near-expiry tiebreak can put B first.
    c, _ = await add_hospital(session, "Hospital C", b.lat, b.lng, org_id=uuid.UUID(int=1))
    b_batch = await add_batch(session, b, iv, on_hand=1000, safety_stock=200, expiry_days=55)
    await add_batch(session, e, iv, on_hand=120, expiry_days=200)  # usable 120
    await add_batch(session, c, iv, on_hand=1000, safety_stock=200, expiry_days=180)
    session.add_all(ProductAuthorization(org_id=o.id, product_id=iv.id) for o in (b, c, e))
    await load_series(session, b, "Hospital B", iv)
    await load_series(session, e, "Hospital E", iv)

    # The forecast job: statsmodels, in the hub.
    summary = await service.run(session, [b.id, e.id], today())
    assert (summary.series, summary.models) == (2, {"holt-winters-weekly/1": 2})

    # 1. B's batch is flagged: expiry-risk excess 300 +- 5, offered up to its transferable.
    b_client = await client_for(world.users["b.STORE_MANAGER"])
    (b_iv,) = (await b_client.get("/forecasts", params={"product_id": str(iv.id)})).json()["items"]
    assert b_iv["synthetic_history"] is True
    (risk,) = b_iv["expiry_risks"]
    assert risk["batch_id"] == str(b_batch.id)
    assert risk["usage_before_expiry"] == pytest.approx(500, abs=5)
    assert abs(risk["excess"] - 300) <= 5
    assert (risk["transferable"], risk["suggested_qty"]) == (800, risk["excess"])
    assert len(b_iv["days"]) == 30

    # E's predicted stock-out: in 4 +- 1 days.
    e_client = await client_for(e_users["STORE_MANAGER"])
    (e_iv,) = (await e_client.get("/forecasts")).json()["items"]
    stockout = datetime.fromisoformat(e_iv["stockout_date"]).date()
    assert abs((stockout - today()).days - 4) <= 1
    assert e_iv["usable_stock"] == 120

    # B posts the suggested surplus; it matches E's forecast stock-out at once.
    r = await b_client.post(
        "/surplus", json={"batch_id": risk["batch_id"], "qty": risk["suggested_qty"]}
    )
    assert r.status_code == 201, r.text
    post = r.json()
    assert (post["status"], post["matched_org_ids"]) == ("MATCHED", [str(e.id)])
    (b_iv,) = (await b_client.get("/forecasts")).json()["items"]
    assert b_iv["expiry_risks"][0]["surplus_post_id"] == post["id"]

    # 2. `surplus.matched` reaches Hospital E's event stream (and B's), not C's.
    await events.publish_pending(session, redis)
    e_events, _ = await events.missed(session, e.id, 0)
    matched = [p for _, p in e_events if p["type"] == "surplus.matched"]
    assert [p["data"] for p in matched] == [{"surplus_id": post["id"], "org_id": str(e.id)}]
    b_events, _ = await events.missed(session, b.id, 0)
    assert any(p["type"] == "surplus.matched" for _, p in b_events)
    c_events, _ = await events.missed(session, c.id, 0)
    assert not any(p["type"] == "surplus.matched" for _, p in c_events)
    (offer,) = (await e_client.get("/surplus/incoming")).json()["items"]
    assert (offer["org_name"], offer["offered_qty"], offer["match"]["kind"]) == (
        "Hospital B",
        risk["suggested_qty"],
        "FORECAST",
    )

    # 3. E's ROUTINE shortage for 300: B ranks first over the equal-cost C (near expiry).
    r = await e_client.post(
        "/shortages",
        json={
            "facility_id": str((await facility_of(session, e)).id),
            "product_id": str(iv.id),
            "qty_required": 300,
            "qty_local_usable": 0,
            "required_by": (datetime.now(UTC) + timedelta(hours=48)).isoformat(),
            "priority": "ROUTINE",
        },
    )
    assert r.status_code == 201, r.text
    run = await session.scalar(select(MatchRun).where(MatchRun.shortage_id == r.json()["id"]))
    assert run is not None
    ranked = {
        cand.source_org_id: cand
        for cand in await session.scalars(select(Candidate).where(Candidate.match_run_id == run.id))
    }
    assert ranked[b.id].landed_cost_paise == ranked[c.id].landed_cost_paise  # equal cost
    assert (ranked[b.id].rank, ranked[c.id].rank) == (1, 2)
    assert run.planned_resolution is not None
    assert run.planned_resolution["type"] == "TRANSFER"
    assert [line["source_org_id"] for line in run.planned_resolution["lines"]] == [str(b.id)]
