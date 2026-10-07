"""The deadline timer with a frozen clock (`now` is passed in): response and hold deadlines,
the release-and-rematch that follows, and idempotency across reruns."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.catalog.models import Product
from app.conftest import World
from app.orgs.models import Organization, OrgType
from app.shortages.models import MatchRun, Priority, Shortage
from app.source_requests import service
from app.source_requests.models import Hold, SourceRequest
from app.source_requests.tests.conftest import (
    Orgs,
    add_batch,
    add_offer,
    add_org,
    add_user,
    authorize,
    create_shortage,
    requests_of,
)

pytestmark = pytest.mark.anyio


async def runs_of(session: AsyncSession, shortage: Shortage) -> list[MatchRun]:
    stmt = select(MatchRun).where(MatchRun.shortage_id == shortage.id)
    return list(await session.scalars(stmt.order_by(MatchRun.run_no)))


async def last_audit(session: AsyncSession, entity_id: object) -> AuditLog:
    stmt = select(AuditLog).where(AuditLog.entity_id == entity_id)
    row = await session.scalar(stmt.order_by(AuditLog.ts.desc()).limit(1))
    assert row is not None
    return row


async def split_world(
    session: AsyncSession, ska: Product, now: datetime
) -> tuple[Organization, Organization, Organization]:
    """Hospitals P (500) and Q (350) cover 850 only together; Supplier Y is the fallback."""
    p = await add_org(session, "Hospital P", OrgType.HOSPITAL, 12.95, 77.60)
    q = await add_org(session, "Hospital Q", OrgType.HOSPITAL, 12.96, 77.60)
    y = await add_org(session, "Supplier Y", OrgType.SUPPLIER, 12.85, 77.66)
    await add_batch(session, p, ska, now, on_hand=500, expiry_days=180)
    await add_batch(session, q, ska, now, on_hand=350, expiry_days=180)
    add_offer(session, y, ska, now, price=2800, lead=22, qty=2000)
    authorize(session, ska, p, q, y)
    await session.flush()
    return p, q, y


async def test_a_critical_request_expires_after_15_minutes_and_its_holds_are_released(
    session: AsyncSession, world: World, products: dict[str, Product], now: datetime
) -> None:
    """The acceptance test: P accepts (holds), Q never answers. At +15 min Q expires, P's
    holds are released, a new match run exists, and the reason is SYSTEM."""
    ska = products["SURG-KIT-A"]
    p, q, y = await split_world(session, ska, now)
    shortage = await create_shortage(session, world.users["a.REQUESTER"], ska, now)
    by_org = {sr.source_org_id: sr for sr in await requests_of(session, shortage)}
    p_user = await add_user(session, p, "STORE_MANAGER", "sm@p.test")
    await service.accept(session, p_user, by_org[p.id].id, None, now=now + timedelta(minutes=1))
    (p_hold,) = await session.scalars(select(Hold).where(Hold.source_request_id == by_org[p.id].id))

    assert await service.expire_overdue(session, now + timedelta(minutes=14, seconds=59)) == 0
    assert (by_org[q.id].status, p_hold.status) == ("REQUESTED", "TENTATIVE")

    assert await service.expire_overdue(session, now + timedelta(minutes=15)) == 1
    assert by_org[q.id].status == "EXPIRED"
    expired = await last_audit(session, by_org[q.id].id)
    assert (expired.after, expired.actor_id, expired.reason, expired.reason_source) == (
        {"status": "EXPIRED"},
        None,
        "Response deadline passed.",
        "SYSTEM",
    )
    await session.refresh(p_hold)
    assert p_hold.status == "RELEASED"
    released = await last_audit(session, p_hold.id)
    assert (released.reason, released.reason_source, released.actor_id) == (
        "Response deadline passed.",
        "SYSTEM",
        None,
    )
    assert by_org[p.id].status == "SUPERSEDED"
    run = (await runs_of(session, shortage))[-1]
    assert (run.run_no, run.triggered_by, run.excluded_org_ids) == (2, "EXPIRY", [q.id])
    run_row = await last_audit(session, run.id)
    assert (run_row.reason, run_row.reason_source) == ("Response deadline passed.", "SYSTEM")
    # Without Q, P's 500 cannot cover 850: BUY from Supplier Y.
    assert run.planned_resolution is not None
    assert run.planned_resolution["type"] == "BUY"
    assert run.planned_resolution["lines"][0]["source_org_id"] == str(y.id)


async def test_scenario_1_b_not_answering_in_15_minutes_falls_back_to_buy(
    session: AsyncSession, world: World, products: dict[str, Product], s1: Orgs, now: datetime
) -> None:
    shortage = await create_shortage(
        session, world.users["a.REQUESTER"], products["SURG-KIT-A"], now
    )
    (request_b,) = await requests_of(session, shortage)
    assert await service.expire_overdue(session, now + timedelta(minutes=15)) == 1
    assert request_b.status == "EXPIRED"
    run = (await runs_of(session, shortage))[-1]
    assert run.excluded_org_ids == [world.hospital_b.id]
    assert run.planned_resolution is not None
    assert run.planned_resolution["type"] == "BUY"
    assert run.planned_resolution["lines"][0]["source_org_id"] == str(s1["Supplier Y"].id)


async def test_a_routine_request_waits_four_hours(
    session: AsyncSession, world: World, products: dict[str, Product], s1: Orgs, now: datetime
) -> None:
    shortage = await create_shortage(
        session,
        world.users["a.REQUESTER"],
        products["SURG-KIT-A"],
        now,
        priority=Priority.ROUTINE,
    )
    (request_b,) = await requests_of(session, shortage)
    assert await service.expire_overdue(session, now + timedelta(hours=3, minutes=59)) == 0
    assert await service.expire_overdue(session, now + timedelta(hours=4)) == 1
    assert request_b.status == "EXPIRED"


async def test_a_tentative_hold_lapses_after_30_minutes(
    session: AsyncSession, world: World, products: dict[str, Product], s1: Orgs, now: datetime
) -> None:
    shortage = await create_shortage(
        session, world.users["a.REQUESTER"], products["SURG-KIT-A"], now
    )
    (request_b,) = await requests_of(session, shortage)
    await service.accept(session, world.users["b.STORE_MANAGER"], request_b.id, None, now=now)
    # Past the response deadline but within the hold: nothing to do.
    assert await service.expire_overdue(session, now + timedelta(minutes=29)) == 0
    assert await service.expire_overdue(session, now + timedelta(minutes=30)) == 1
    assert request_b.status == "EXPIRED"
    row = await last_audit(session, request_b.id)
    assert (row.before, row.after, row.reason, row.reason_source) == (
        {"status": "TENTATIVE_HOLD"},
        {"status": "EXPIRED"},
        "Hold deadline passed.",
        "SYSTEM",
    )
    held = await session.scalars(select(Hold.status).where(Hold.source_request_id == request_b.id))
    assert set(held) == {"RELEASED"}
    # Nobody declined, so B is not excluded and is asked again.
    run = (await runs_of(session, shortage))[-1]
    assert (run.run_no, run.excluded_org_ids) == (2, [])
    requests = await requests_of(session, shortage)
    assert [(r.source_org_id, r.status) for r in requests] == [
        (world.hospital_b.id, "EXPIRED"),
        (world.hospital_b.id, "REQUESTED"),
    ]
    assert requests[1].sla_deadline == now + timedelta(minutes=30 + 15)


async def test_rerunning_the_timer_never_double_processes(
    session: AsyncSession, world: World, products: dict[str, Product], s1: Orgs, now: datetime
) -> None:
    """Deadlines live in the database: a second pass (another worker, or the same one after a
    restart) finds the request already expired and does nothing."""
    shortage = await create_shortage(
        session, world.users["a.REQUESTER"], products["SURG-KIT-A"], now
    )
    (request_b,) = await requests_of(session, shortage)
    later = now + timedelta(minutes=20)
    assert await service.expire_overdue(session, later) == 1
    assert await service.expire_overdue(session, later) == 0
    assert await service.expire_one(session, request_b.id, later) is False
    expiries = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.entity_id == request_b.id, AuditLog.after["status"].astext == "EXPIRED")
    )
    assert expiries == 1
    assert [r.run_no for r in await runs_of(session, shortage)] == [1, 2]


async def test_answered_requests_are_not_touched_by_the_timer(
    session: AsyncSession, world: World, products: dict[str, Product], s1: Orgs, now: datetime
) -> None:
    shortage = await create_shortage(
        session, world.users["a.REQUESTER"], products["SURG-KIT-A"], now
    )
    (request_b,) = await requests_of(session, shortage)
    await service.decline(session, world.users["b.STORE_MANAGER"], request_b.id, None, now=now)
    assert await service.expire_overdue(session, now + timedelta(days=2)) == 0
    assert request_b.status == "DECLINED"
    assert await session.scalar(
        select(func.count()).select_from(SourceRequest).where(SourceRequest.status == "EXPIRED")
    ) == 0  # fmt: skip
