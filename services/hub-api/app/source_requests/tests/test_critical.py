"""CRITICAL parallel requests (business-rules.md §7 step 2, §8 SUPERSEDED, §10; S19): a
CRITICAL TRANSFER asks up to 3 single sources at once, the first accept wins and the rest are
SUPERSEDED in the same transaction. A ROUTINE shortage still asks one source at a time. The
race on a committed database is in `test_concurrency.py`."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.auth.models import User
from app.catalog.models import Product
from app.conftest import ClientFor, World
from app.domain.events import EventType
from app.events.models import EventOutbox
from app.orgs.models import Organization, OrgType
from app.recommendations import transitions
from app.shortages.models import Candidate, MatchRun, Priority, Shortage
from app.source_requests import service
from app.source_requests.models import Hold, SourceRequest
from app.source_requests.tests.conftest import (
    add_batch,
    add_org,
    add_user,
    authorize,
    create_shortage,
    requests_of,
)

pytestmark = pytest.mark.anyio
FIRST = "Another source confirmed first."


@dataclass
class Network:
    shortage: Shortage
    by_rank: list[Organization]  # the four hospitals, in the run's rank order
    managers: dict[uuid.UUID, User]  # org id -> its store manager


async def network(
    session: AsyncSession, world: World, product: Product, now: datetime, priority: Priority
) -> Network:
    """Four hospitals near Hospital A, each able to cover the 850 shortfall alone."""
    orgs, managers = [], {}
    for n in range(4):
        org = await add_org(session, f"Hospital P{n + 1}", OrgType.HOSPITAL, 12.95 + n / 100, 77.6)
        await add_batch(session, org, product, now, on_hand=1000, expiry_days=180)
        managers[org.id] = await add_user(session, org, "STORE_MANAGER", f"sm@p{n + 1}.test")
        orgs.append(org)
    authorize(session, product, *orgs)
    await session.flush()
    shortage = await create_shortage(
        session, world.users["a.REQUESTER"], product, now, priority=priority,
        qty_required=850, qty_local_usable=0,
    )  # fmt: skip
    ranked = await session.scalars(
        select(Candidate.source_org_id)
        .join(MatchRun, MatchRun.id == Candidate.match_run_id)
        .where(MatchRun.shortage_id == shortage.id, Candidate.eligible.is_(True))
        .order_by(Candidate.rank)
    )
    by_id = {o.id: o for o in orgs}
    return Network(shortage, [by_id[org_id] for org_id in ranked], managers)


@pytest.fixture
async def critical(
    session: AsyncSession, world: World, products: dict[str, Product], now: datetime
) -> Network:
    return await network(session, world, products["SURG-KIT-A"], now, Priority.CRITICAL)


async def runs_of(session: AsyncSession, shortage: Shortage) -> list[MatchRun]:
    stmt = select(MatchRun).where(MatchRun.shortage_id == shortage.id)
    return list(await session.scalars(stmt.order_by(MatchRun.run_no)))


async def request_to(session: AsyncSession, shortage: Shortage, org: Organization) -> SourceRequest:
    rows = [r for r in await requests_of(session, shortage) if r.source_org_id == org.id]
    return rows[-1]


async def answer(
    client_for: ClientFor, net: Network, session: AsyncSession, org: Organization, action: str
) -> httpx.Response:
    sr = await request_to(session, net.shortage, org)
    client = await client_for(net.managers[org.id])
    return await client.post(f"/source-requests/{sr.id}/{action}", json={})


async def fresh_statuses(session: AsyncSession, shortage: Shortage) -> dict[uuid.UUID, str]:
    rows = await session.scalars(
        select(SourceRequest)
        .where(SourceRequest.shortage_id == shortage.id)
        .execution_options(populate_existing=True)
    )
    return {r.source_org_id: r.status for r in rows}


# --- sending -----------------------------------------------------------------------------------


async def test_a_critical_transfer_asks_the_top_three_single_sources_at_once(
    session: AsyncSession, world: World, critical: Network, now: datetime
) -> None:
    requests = await requests_of(session, critical.shortage)
    top3 = {o.id for o in critical.by_rank[:3]}
    assert {r.source_org_id for r in requests} == top3  # the fourth is not asked
    assert {(r.status, r.qty, r.sla_deadline) for r in requests} == {
        ("REQUESTED", 850, now + timedelta(minutes=15))
    }
    (run,) = await runs_of(session, critical.shortage)
    plan = run.planned_resolution
    assert plan is not None and plan["type"] == "TRANSFER"
    assert [x["source_org_id"] for x in plan["parallel"]] == [
        str(o.id) for o in critical.by_rank[:3]
    ]
    assert plan["lines"] == plan["parallel"][:1]  # the planned line is the top-ranked
    # One `source_request.created` per source, to that source and the requester only.
    created = list(
        await session.scalars(
            select(EventOutbox).where(
                EventOutbox.event_type == EventType.SOURCE_REQUEST_CREATED,
                EventOutbox.payload["data"]["shortage_id"].astext == str(critical.shortage.id),
            )
        )
    )
    assert sorted(sorted(map(str, e.org_ids)) for e in created) == sorted(
        sorted([str(world.hospital_a.id), str(org_id)]) for org_id in top3
    )


async def test_a_routine_transfer_still_asks_one_source_at_a_time(
    session: AsyncSession, world: World, products: dict[str, Product], now: datetime
) -> None:
    net = await network(session, world, products["SURG-KIT-A"], now, Priority.ROUTINE)
    (sr,) = await requests_of(session, net.shortage)
    assert sr.source_org_id == net.by_rank[0].id
    (run,) = await runs_of(session, net.shortage)
    assert run.planned_resolution is not None and run.planned_resolution["parallel"] == []


# --- first accept wins -------------------------------------------------------------------------


async def test_the_first_accept_wins_and_the_others_are_superseded(
    session: AsyncSession, world: World, critical: Network, client_for: ClientFor
) -> None:
    first, winner, third, _ = critical.by_rank
    r = await answer(client_for, critical, session, winner, "accept")
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["held_qty"]) == ("TENTATIVE_HOLD", 850)

    statuses = await fresh_statuses(session, critical.shortage)
    assert statuses == {first.id: "SUPERSEDED", winner.id: "TENTATIVE_HOLD", third.id: "SUPERSEDED"}
    losers = [await request_to(session, critical.shortage, o) for o in (first, third)]
    loser_holds = await session.scalars(
        select(Hold).where(Hold.source_request_id.in_([sr.id for sr in losers]))
    )
    assert list(loser_holds) == []  # nothing held for a superseded request

    # §10: the supersede is a SYSTEM change of the request, in the requester's org.
    for sr in losers:
        (row,) = await session.scalars(
            select(AuditLog).where(
                AuditLog.entity_id == sr.id, AuditLog.after["status"].astext == "SUPERSEDED"
            )
        )
        assert (row.org_id, row.actor_id, row.reason_source, row.reason) == (
            world.hospital_a.id, None, "SYSTEM", FIRST,
        )  # fmt: skip
    # The winner's accept is in its own org with its user, mirrored to the requester without.
    won = await request_to(session, critical.shortage, winner)
    accepted = list(
        await session.scalars(
            select(AuditLog).where(
                AuditLog.entity_id == won.id, AuditLog.after["status"].astext == "TENTATIVE_HOLD"
            )
        )
    )
    assert sorted((str(a.org_id), a.actor_id is None) for a in accepted) == sorted(
        [(str(winner.id), False), (str(world.hospital_a.id), True)]
    )
    # The losing hospitals hear about it (their apps show "No longer needed").
    changed = await session.scalars(
        select(EventOutbox).where(
            EventOutbox.event_type == EventType.SOURCE_REQUEST_STATUS_CHANGED,
            EventOutbox.payload["data"]["to"].astext == "SUPERSEDED",
        )
    )
    assert sorted(sorted(map(str, e.org_ids)) for e in changed) == sorted(
        sorted([str(world.hospital_a.id), str(o.id)]) for o in (first, third)
    )

    # §7 step 4: the plan's one source holds stock, so the recommendation names the winner.
    rec = await transitions.open_for(session, critical.shortage.id)
    assert rec is not None and rec.type == "TRANSFER"
    assert [line["source_org_id"] for line in rec.lines] == [str(winner.id)]
    assert f"3 sources were asked at once because the shortage is CRITICAL; {winner.name} " \
        "accepted first." in rec.explanation  # fmt: skip
    await session.refresh(critical.shortage)
    assert critical.shortage.status == "AWAITING_DECISION"


async def test_a_superseded_request_can_no_longer_be_answered(
    session: AsyncSession, critical: Network, client_for: ClientFor
) -> None:
    first, winner, *_ = critical.by_rank
    assert (await answer(client_for, critical, session, winner, "accept")).status_code == 200
    for action in ("accept", "decline"):
        r = await answer(client_for, critical, session, first, action)
        assert r.status_code == 409, r.text
        assert r.json()["code"] == "invalid_transition"
        assert r.json()["details"]["from"] == "SUPERSEDED"
    # Another org's request: 403, whatever its state.
    loser = await request_to(session, critical.shortage, first)
    r = await (await client_for(critical.managers[winner.id])).post(
        f"/source-requests/{loser.id}/accept", json={}
    )
    assert r.status_code == 403


async def test_the_losing_hospital_reads_its_request_as_superseded(
    session: AsyncSession, critical: Network, client_for: ClientFor
) -> None:
    first, winner, *_ = critical.by_rank
    assert (await answer(client_for, critical, session, winner, "accept")).status_code == 200
    r = await (await client_for(critical.managers[first.id])).get(
        "/source-requests", params={"direction": "incoming"}
    )
    assert r.status_code == 200
    (item,) = r.json()["items"]
    assert (item["status"], item["held_qty"]) == ("SUPERSEDED", 0)


# --- declines and expiries ---------------------------------------------------------------------


async def test_a_decline_waits_for_the_other_parallel_requests(
    session: AsyncSession, critical: Network, client_for: ClientFor
) -> None:
    """§8 lists no SUPERSEDED trigger for a parallel sibling's decline, so the others keep
    their chance; once all three have declined, matching re-runs without any of them."""
    a, b, c, d = critical.by_rank
    for n, org in enumerate((a, b), 1):
        assert (await answer(client_for, critical, session, org, "decline")).status_code == 200
        assert len(await runs_of(session, critical.shortage)) == 1
        statuses = await fresh_statuses(session, critical.shortage)
        assert sum(s == "REQUESTED" for s in statuses.values()) == 3 - n
    assert (await answer(client_for, critical, session, c, "decline")).status_code == 200

    runs = await runs_of(session, critical.shortage)
    assert [r.triggered_by for r in runs] == ["CREATE", "DECLINE"]
    assert {a.id, b.id, c.id} <= set(runs[-1].excluded_org_ids)
    open_now = [r for r in await requests_of(session, critical.shortage) if r.status == "REQUESTED"]
    assert [r.source_org_id for r in open_now] == [d.id]


async def test_a_source_that_declined_before_another_won_stays_excluded(
    session: AsyncSession, world: World, critical: Network, client_for: ClientFor
) -> None:
    a, b, c, d = critical.by_rank
    assert (await answer(client_for, critical, session, a, "decline")).status_code == 200
    assert (await answer(client_for, critical, session, b, "accept")).status_code == 200
    assert (await fresh_statuses(session, critical.shortage))[c.id] == "SUPERSEDED"
    rec = await transitions.open_for(session, critical.shortage.id)
    assert rec is not None
    approver = await client_for(world.users["a.APPROVER"])
    assert (await approver.post(f"/recommendations/{rec.id}/reject", json={})).status_code == 200

    run = (await runs_of(session, critical.shortage))[-1]
    assert set(run.excluded_org_ids) == {a.id, b.id}  # declined, and the rejected plan's source
    asked = [r for r in await requests_of(session, critical.shortage) if r.status == "REQUESTED"]
    assert {r.source_org_id for r in asked} == {c.id, d.id}  # superseded is not excluded


async def test_parallel_requests_that_all_expire_rematch_once(
    session: AsyncSession, critical: Network, now: datetime
) -> None:
    first = await requests_of(session, critical.shortage)
    assert await service.expire_overdue(session, now + timedelta(minutes=16)) == 3
    statuses = {
        r.id: r.status
        for r in await session.scalars(
            select(SourceRequest)
            .where(SourceRequest.id.in_([r.id for r in first]))
            .execution_options(populate_existing=True)
        )
    }
    assert set(statuses.values()) == {"EXPIRED"}  # each its own expiry, none superseded
    runs = await runs_of(session, critical.shortage)
    assert [r.triggered_by for r in runs] == ["CREATE", "EXPIRY"]
    assert runs[-1].excluded_org_ids == []  # expired -> not excluded
    again = [r for r in await requests_of(session, critical.shortage) if r.status == "REQUESTED"]
    assert {r.source_org_id for r in again} == {o.id for o in critical.by_rank[:3]}


async def test_cancelling_supersedes_every_parallel_request(
    session: AsyncSession, world: World, critical: Network, client_for: ClientFor
) -> None:
    requester = await client_for(world.users["a.REQUESTER"])
    r = await requester.post(f"/shortages/{critical.shortage.id}/cancel", json={})
    assert r.status_code == 200, r.text
    statuses = await fresh_statuses(session, critical.shortage)
    assert set(statuses.values()) == {"SUPERSEDED"}
