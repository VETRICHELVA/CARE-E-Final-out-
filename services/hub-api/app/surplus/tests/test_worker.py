"""The S18 worker jobs: the nightly forecast + surplus match, and surplus expiry. The jobs get
the test session through a stand-in sessionmaker, so everything still rolls back."""

import runpy
from datetime import timedelta
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker
from app.catalog.models import Product
from app.conftest import World
from app.forecasting.models import ConsumptionRecord, Forecast
from app.surplus.models import SurplusMatch, SurplusPost
from app.surplus.tests.conftest import add_batch, today

pytestmark = pytest.mark.anyio

SEED_DIR = Path(__file__).resolve().parents[5] / "scripts" / "seed"


class Maker:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def __call__(self) -> "Maker":
        return self

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(
        self, t: type[BaseException] | None, e: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None


def test_the_jobs_are_scheduled() -> None:
    names = {job.name for job in worker.WorkerSettings.cron_jobs}
    assert {"cron:nightly_forecasts", "cron:expire_surplus"} <= names


async def test_nightly_forecasts_then_matches_open_surplus(
    session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    iv = products["IV-CAN-20G"]
    day = today()
    session.add_all(
        ConsumptionRecord(
            org_id=world.hospital_a.id,
            product_id=iv.id,
            date=day - timedelta(days=i),
            qty=30,
            synthetic=True,
        )
        for i in range(1, 29)
    )
    await add_batch(session, world.hospital_a, iv, on_hand=150, expiry_days=200)  # 5 days
    batch = await add_batch(session, world.hospital_b, iv, on_hand=900, expiry_days=55)
    session.add(
        SurplusPost(
            org_id=world.hospital_b.id,
            batch_id=batch.id,
            product_id=iv.id,
            qty=300,
            expiry_date=batch.expiry_date,
            status="OPEN",
            created_by=world.users["b.STORE_MANAGER"].id,
        )
    )
    await session.flush()
    ctx: dict[str, Any] = {"sessionmaker": Maker(session)}
    assert await worker.nightly_forecasts(ctx) == 1
    # Far enough to judge A's batch, which expires in 200 days.
    assert (
        await session.scalar(
            select(func.count()).select_from(Forecast).where(Forecast.org_id == world.hospital_a.id)
        )
        == 200
    )
    match = await session.scalar(select(SurplusMatch))
    assert match is not None
    assert (match.org_id, match.kind) == (world.hospital_a.id, "FORECAST")
    assert match.stockout_date == day + timedelta(days=5)
    assert await worker.nightly_forecasts(ctx) == 0  # matched once


async def test_expire_surplus_job(
    session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    iv = products["IV-CAN-20G"]
    batch = await add_batch(session, world.hospital_b, iv, on_hand=900, expiry_days=0)
    post = SurplusPost(
        org_id=world.hospital_b.id,
        batch_id=batch.id,
        product_id=iv.id,
        qty=300,
        expiry_date=batch.expiry_date,
        status="MATCHED",
        created_by=world.users["b.STORE_MANAGER"].id,
    )
    session.add(post)
    await session.flush()
    assert await worker.expire_surplus({"sessionmaker": Maker(session)}) == 1
    assert post.status == "EXPIRED"


async def test_the_seed_history_is_synthetic_and_idempotent(
    session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    load = runpy.run_path(str(SEED_DIR / "consumption.py"))["load"]
    iv = products["IV-CAN-20G"]
    day = today()
    reported = ConsumptionRecord(
        org_id=world.hospital_a.id,
        product_id=iv.id,
        date=day - timedelta(days=1),
        qty=7,
        synthetic=False,
    )
    session.add(reported)
    await session.flush()
    first = await load(session, day)
    assert first == {"Hospital A": 15 * 365, "Hospital B": 15 * 365}
    assert await load(session, day) == first

    async def count(synthetic: bool) -> int:
        stmt = select(func.count()).select_from(ConsumptionRecord)
        return int(
            await session.scalar(stmt.where(ConsumptionRecord.synthetic.is_(synthetic))) or 0
        )

    # One reported row wins over the synthetic one for that day; nothing doubles.
    assert (await count(True), await count(False)) == (2 * 15 * 365 - 1, 1)
    await session.refresh(reported)
    assert reported.qty == 7
