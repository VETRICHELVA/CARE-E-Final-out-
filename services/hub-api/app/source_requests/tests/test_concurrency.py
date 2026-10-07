"""Races on a database whose transactions really commit: two accepts for the same batch,
two timer workers at once, and the arq job after a restart. Each test makes its own product
and orgs, so rows other tests committed never take part in its matching."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import AuditLog
from app.auth import service as auth_service
from app.auth.models import User
from app.catalog.models import Product
from app.db import get_session
from app.domain.source_request import ACTIVE_HOLD
from app.main import create_app
from app.orgs.models import Organization, OrgType
from app.shortages.models import MatchRun, Shortage
from app.source_requests import service
from app.source_requests.models import Hold, SourceRequest
from app.source_requests.tests.conftest import (
    add_batch,
    add_org,
    add_user,
    authorize,
    create_shortage,
    unique,
)
from app.worker import expire_source_requests, startup

pytestmark = pytest.mark.anyio
Maker = async_sessionmaker[AsyncSession]


@dataclass
class Race:
    product: Product
    source: Organization
    source_user: User
    batch_id: uuid.UUID
    shortages: list[Shortage]
    requests: list[SourceRequest]


async def setup(sm: Maker, now: datetime, requesters: int) -> Race:
    """One source with 1,000 transferable; each requester is 850 short, so B is planned for
    every shortage (no holds yet) but can hold stock for only one of them."""
    tag = unique()
    async with sm() as session:
        product = Product(
            code=f"T-{tag}",
            name=f"Test kit {tag}",
            category="Test",
            unit="each",
            default_min_shelf_life_days=30,
        )
        session.add(product)
        source = await add_org(session, f"Source {tag}", OrgType.HOSPITAL, 12.93, 77.62)
        source_user = await add_user(session, source, "STORE_MANAGER", f"sm-{tag}@b.test")
        batch = await add_batch(session, source, product, now, on_hand=1000, expiry_days=180)
        authorize(session, product, source)
        shortages, requests = [], []
        for n in range(requesters):
            org = await add_org(session, f"Requester {n} {tag}", OrgType.HOSPITAL, 12.97, 77.59)
            user = await add_user(session, org, "REQUESTER", f"req{n}-{tag}@a.test")
            shortage = await create_shortage(
                session, user, product, now, qty_required=850, qty_local_usable=0
            )
            (sr,) = await session.scalars(
                select(SourceRequest).where(SourceRequest.shortage_id == shortage.id)
            )
            shortages.append(shortage)
            requests.append(sr)
        await session.commit()
    return Race(product, source, source_user, batch.id, shortages, requests)


@pytest.fixture
async def client(committed: Maker) -> AsyncIterator[httpx.AsyncClient]:
    """The real app, each request on its own committed session (as in production)."""
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


async def test_two_accepts_for_the_same_batch_exactly_one_wins(
    committed: Maker, client: httpx.AsyncClient
) -> None:
    race = await setup(committed, datetime.now(UTC), requesters=2)
    auth = await token(committed, race.source_user)
    first, second = await asyncio.gather(
        *(client.post(f"/source-requests/{sr.id}/accept", headers=auth) for sr in race.requests)
    )
    results = sorted([(first.status_code, first.json()), (second.status_code, second.json())],
                     key=lambda r: r[0])  # fmt: skip
    (ok, won), (lost, err) = results
    assert (ok, won["status"], won["held_qty"]) == (200, "TENTATIVE_HOLD", 850)
    assert (lost, err["code"], err["message"]) == (
        409,
        "conflict",
        "Stock changed before acceptance.",
    )
    assert err["details"] == {"requested_qty": 850, "transferable_qty": 150}

    async with committed() as session:
        held = await session.scalar(
            select(func.sum(Hold.qty)).where(
                Hold.batch_id == race.batch_id, Hold.status.in_(ACTIVE_HOLD)
            )
        )
        assert held == 850  # never more than the batch can give
        statuses = {
            sr.id: sr.status
            for sr in await session.scalars(
                select(SourceRequest).where(SourceRequest.id.in_([r.id for r in race.requests]))
            )
        }
        assert sorted(statuses.values()) == ["EXPIRED", "TENTATIVE_HOLD"]
        loser = next(r for r in race.requests if statuses[r.id] == "EXPIRED")
        reason = await session.scalar(
            select(AuditLog.reason)
            .where(AuditLog.entity_id == loser.id)
            .order_by(AuditLog.ts.desc())
            .limit(1)
        )
        assert reason == "Stock changed before acceptance."
        runs = list(
            await session.scalars(
                select(MatchRun.triggered_by)
                .where(MatchRun.shortage_id == loser.shortage_id)
                .order_by(MatchRun.run_no)
            )
        )
        assert runs == ["CREATE", "EXPIRY"]  # the loser's shortage was matched again


async def test_two_timer_workers_at_once_expire_a_request_once(committed: Maker) -> None:
    now = datetime.now(UTC)
    race = await setup(committed, now, requesters=1)
    (sr,) = race.requests
    later = now + timedelta(minutes=15)

    async def worker() -> bool:
        async with committed() as session:
            done = await service.expire_one(session, sr.id, later)
            await session.commit()
            return done

    assert sorted(await asyncio.gather(worker(), worker())) == [False, True]
    async with committed() as session:
        assert (await session.get_one(SourceRequest, sr.id)).status == "EXPIRED"
        expiries = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.entity_id == sr.id, AuditLog.after["status"].astext == "EXPIRED")
        )
        runs = await session.scalar(
            select(func.count()).select_from(MatchRun).where(MatchRun.shortage_id == sr.shortage_id)
        )
        assert (expiries, runs) == (1, 2)


async def test_a_restarted_worker_picks_up_deadlines_from_the_database(committed: Maker) -> None:
    """The request was due while no worker ran; the job finds it on its first (startup) run,
    and a second run finds nothing more for it."""
    race = await setup(committed, datetime.now(UTC) - timedelta(minutes=20), requesters=1)
    (sr,) = race.requests
    ctx: dict[str, object] = {"sessionmaker": committed}
    await startup(ctx)
    assert await expire_source_requests(ctx) >= 1
    await expire_source_requests(ctx)
    async with committed() as session:
        assert (await session.get_one(SourceRequest, sr.id)).status == "EXPIRED"
        expiries = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.entity_id == sr.id, AuditLog.after["status"].astext == "EXPIRED")
        )
        assert expiries == 1


async def test_concurrent_stock_writes_never_lose_a_waiting_shortages_rerun(
    committed: Maker,
) -> None:
    """§5 re-run on a stock change, with two writes in flight: A's write holds the waiting
    shortage's lock while it matches, and cannot see B's uncommitted stock. B's write must wait
    for that lock and re-run after A commits, rather than skip the shortage and lose B's stock."""
    from app.shortages import service as shortages

    now = datetime.now(UTC).replace(microsecond=0)
    tag = unique()
    async with committed() as session:
        product = Product(
            code=f"R-{tag}",
            name=f"Rerun kit {tag}",
            category="Test",
            unit="each",
            default_min_shelf_life_days=30,
        )
        session.add(product)
        requester = await add_org(session, f"Requester {tag}", OrgType.HOSPITAL, 12.97, 77.59)
        user = await add_user(session, requester, "REQUESTER", f"req-{tag}@c.test")
        a = await add_org(session, f"Writer A {tag}", OrgType.HOSPITAL, 12.93, 77.62)
        b = await add_org(session, f"Writer B {tag}", OrgType.HOSPITAL, 12.95, 77.60)
        authorize(session, product, a, b)
        shortage = await create_shortage(
            session, user, product, now, qty_required=500, qty_local_usable=0
        )
        await session.commit()
    async with committed() as session:
        first = await session.scalar(select(MatchRun).where(MatchRun.shortage_id == shortage.id))
        assert first is not None and first.planned_resolution is None  # "No eligible source"

    async with committed() as writer_a:
        # A's stock expires before delivery, so A's own re-run still finds nothing.
        await add_batch(writer_a, a, product, now, on_hand=900, expiry_days=5, batch_no="A-1")
        await shortages.rematch_waiting(writer_a, [product.id], a.id, now=now)

        async def write_b() -> None:
            async with committed() as writer_b:
                await add_batch(
                    writer_b, b, product, now, on_hand=900, expiry_days=180, batch_no="B-1"
                )
                await shortages.rematch_waiting(writer_b, [product.id], b.id, now=now)
                await writer_b.commit()

        b_task = asyncio.create_task(write_b())
        await asyncio.sleep(0.5)
        assert not b_task.done(), "B's write must wait for the shortage, not skip it"
        await writer_a.commit()
    await asyncio.wait_for(b_task, timeout=10)

    async with committed() as session:
        runs = list(
            await session.scalars(
                select(MatchRun)
                .where(MatchRun.shortage_id == shortage.id)
                .order_by(MatchRun.run_no)
            )
        )
        assert [r.triggered_by for r in runs] == ["CREATE", "STOCK_CHANGE", "STOCK_CHANGE"]
        plan = runs[-1].planned_resolution
        assert plan is not None and plan["type"] == "TRANSFER"
        assert [line["source_org_id"] for line in plan["lines"]] == [str(b.id)]
