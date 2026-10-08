"""GET /ai/read/*: each copilot tool reads Scenario 1 as the user it acts for would, with the
same org scope (403 for another org) and the same redaction as the user's own endpoints."""

import re

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tests.conftest import AiClientFor
from app.auth.models import User
from app.conftest import ClientFor, World
from app.receiving.tests.conftest import receipt
from app.recommendations.tests.conftest import answer_b, open_rec
from app.shipments.models import Shipment
from app.shortages.models import Candidate, MatchRun, Shortage
from app.source_requests.tests.conftest import Orgs

pytestmark = pytest.mark.anyio


async def candidates_by_name(ai: httpx.AsyncClient, shortage: Shortage) -> dict[str, dict]:  # type: ignore[type-arg]
    r = await ai.get(f"/ai/read/shortages/{shortage.id}/match-run")
    assert r.status_code == 200, r.text
    return {c["source_org_name"]: c for c in r.json()["candidates"]}


# --- Scenario 1 step 2: the first match ----------------------------------------------------------


async def test_shortage_and_match_run_as_the_approver(
    ai_client_for: AiClientFor, world: World, shortage: Shortage
) -> None:
    ai = await ai_client_for(world.users["a.APPROVER"])
    r = await ai.get(f"/ai/read/shortages/{shortage.id}")
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["qty_required"], out["qty_local_usable"], out["shortfall"]) == (1000, 150, 850)
    assert (out["product_code"], out["product_name"]) == ("SURG-KIT-A", "Surgical Kit A")
    assert out["priority"] == "CRITICAL" and out["status"] == "MATCHING"
    (request,) = out["source_requests"]
    assert (request["source_org_name"], request["status"], request["qty"]) == (
        "Hospital B",
        "REQUESTED",
        850,
    )
    assert request["holds"] is None and request["responded_by"] is None  # requester's view
    assert out["recommendations"] == [] and out["shipments"] == []

    by_name = await candidates_by_name(ai, shortage)
    assert by_name["Hospital B"]["rank"] == 1 and by_name["Hospital B"]["transferable_qty"] == 1000
    reasons = {
        name: [g["reason"] for g in c["gate_results"] if not g["passed"]]
        for name, c in by_name.items()
    }
    assert reasons["Hospital C"] == ["Only 100 transferable; 850 needed"]
    # 12 days at delivery, or 11 when delivery lands after midnight UTC (S05 -> S20 note).
    assert re.fullmatch(r"Expires in 1[12] days; 30 required", reasons["Hospital D"][0])
    assert "authorized" in reasons["Hospital E"][0].lower()
    for name in ("Hospital B", "Hospital C", "Hospital D", "Hospital E"):
        assert by_name[name]["landed_cost_paise"] is None  # never a hospital's cost
    assert by_name["Supplier Y"]["landed_cost_paise"] is not None


async def test_candidate(
    session: AsyncSession, ai_client_for: AiClientFor, world: World, shortage: Shortage
) -> None:
    d = await session.scalar(
        select(Candidate)
        .join(MatchRun, MatchRun.id == Candidate.match_run_id)
        .where(MatchRun.shortage_id == shortage.id, Candidate.transferable_qty == 900)
    )
    assert d is not None
    ai = await ai_client_for(world.users["a.APPROVER"])
    r = await ai.get(f"/ai/read/candidates/{d.id}")
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["source_org_name"], out["eligible"], out["landed_cost_paise"]) == (
        "Hospital D",
        False,
        None,
    )
    shelf = next(g for g in out["gate_results"] if g["gate"] == "shelf_life")
    assert shelf["passed"] is False

    b = await ai_client_for(world.users["b.APPROVER"])
    assert (await b.get(f"/ai/read/candidates/{d.id}")).status_code == 403
    r = await ai.get(f"/ai/read/candidates/{shortage.id}")  # not a candidate id
    assert r.status_code == 404


async def test_another_org_gets_403_from_every_shortage_tool(
    ai_client_for: AiClientFor, world: World, shortage: Shortage
) -> None:
    """S13 acceptance: a Hospital B user asking about Hospital A's shortage gets nothing."""
    b = await ai_client_for(world.users["b.APPROVER"])
    for url in (
        f"/ai/read/shortages/{shortage.id}",
        f"/ai/read/shortages/{shortage.id}/match-run",
        f"/ai/read/audit?entity=shortage&entity_id={shortage.id}",
    ):
        r = await b.get(url)
        assert r.status_code == 403, (url, r.text)
        assert r.json()["code"] == "forbidden"
        assert "850" not in r.text and "Hospital D" not in r.text


async def test_capabilities_are_the_users(
    ai_client_for: AiClientFor, world: World, shortage: Shortage
) -> None:
    """The AI has the user's capabilities, no more: a receiver may not read shortages (as
    GET /shortages/{id}) and a requester may not read the audit trail."""
    receiver = await ai_client_for(world.users["a.RECEIVER"])
    r = await receiver.get(f"/ai/read/shortages/{shortage.id}")
    assert r.status_code == 403
    requester = await ai_client_for(world.users["a.REQUESTER"])
    assert (await requester.get(f"/ai/read/shortages/{shortage.id}")).status_code == 200
    r = await requester.get(f"/ai/read/audit?entity=shortage&entity_id={shortage.id}")
    assert r.status_code == 403
    assert r.json()["details"] == {"capabilities": ["audit.read"]}


# --- Scenario 1 steps 3-4: B declines, BUY from Supplier Y --------------------------------------


async def test_recommendation_after_bs_decline(
    session: AsyncSession,
    ai_client_for: AiClientFor,
    client_for: ClientFor,
    world: World,
    s1: Orgs,
    shortage: Shortage,
) -> None:
    await answer_b(session, await client_for(world.users["b.STORE_MANAGER"]), shortage, "decline")
    rec = await open_rec(session, shortage)
    ai = await ai_client_for(world.users["a.APPROVER"])

    r = await ai.get(f"/ai/read/recommendations/{rec.id}")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["type"] == "BUY" and out["status"] == "PENDING"
    assert [line["source_org_name"] for line in out["lines"]] == ["Supplier Y"]
    assert [line["source_org_name"] for line in out["alternatives"]] == ["Supplier X"]
    assert out["lines"][0]["unit_price_paise"] == 2800

    view = (await ai.get(f"/ai/read/shortages/{shortage.id}")).json()
    (declined,) = view["source_requests"]
    assert (declined["status"], declined["reason_source"]) == ("DECLINED", "SYSTEM")
    assert declined["decline_reason"] is None  # the audit row carries the wording
    assert [(x["id"], x["type"]) for x in view["recommendations"]] == [(str(rec.id), "BUY")]

    trail = await ai.get(f"/ai/read/audit?entity=shortage&entity_id={shortage.id}")
    assert trail.status_code == 200, trail.text
    rows = trail.json()["items"]
    assert any(
        row["entity"] == "source_request" and row["reason"] == "No reason was entered."
        for row in rows
    )
    one = await ai.get(f"/ai/read/audit?entity=recommendation&entity_id={rec.id}")
    assert [row["action"] for row in one.json()["items"]] == ["recommendation.created"]

    b = await ai_client_for(world.users["b.APPROVER"])
    assert (await b.get(f"/ai/read/recommendations/{rec.id}")).status_code == 403
    r = await b.get(f"/ai/read/audit?entity=recommendation&entity_id={rec.id}")
    assert r.status_code == 200 and r.json()["items"] == []  # B's own rows only: none


async def test_a_transfer_recommendation_hides_the_hospital_cost(
    session: AsyncSession,
    ai_client_for: AiClientFor,
    client_for: ClientFor,
    world: World,
    shortage: Shortage,
) -> None:
    """S09 -> S13: the AI reads RecommendationOut's redaction, never the stored lines."""
    await answer_b(session, await client_for(world.users["b.STORE_MANAGER"]), shortage, "accept")
    rec = await open_rec(session, shortage)
    assert rec.lines[0]["landed_cost_paise"] is not None  # stored, for the hub's ranking
    ai = await ai_client_for(world.users["a.APPROVER"])
    out = (await ai.get(f"/ai/read/recommendations/{rec.id}")).json()
    assert out["type"] == "TRANSFER"
    (line,) = out["lines"]
    assert line["source_org_name"] == "Hospital B"
    assert line["landed_cost_paise"] is None and line["unit_price_paise"] is None
    assert out["total_landed_cost_paise"] is None
    assert str(rec.lines[0]["landed_cost_paise"]) not in (await ai.get(
        f"/ai/read/recommendations/{rec.id}"
    )).text  # fmt: skip


# --- Scenario 1 steps 6-7: delivery, receipt, residual ------------------------------------------


async def test_shipment_receipt_and_coldchain(
    session: AsyncSession,
    ai_client_for: AiClientFor,
    world: World,
    shortage: Shortage,
    po_delivered: Shipment,
    receiver: httpx.AsyncClient,
) -> None:
    r = await receiver.post(
        f"/shipments/{po_delivered.id}/receipt", json=receipt(790, 790, batch_no="Y-0042")
    )
    assert r.status_code == 201, r.text
    ai = await ai_client_for(world.users["a.APPROVER"])

    r = await ai.get(f"/ai/read/shipments/{po_delivered.id}")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "RECONCILED" and out["route_geometry"] is None
    got = out["receipt"]
    assert (got["expected"], got["received"], got["accepted"], got["rejected"]) == (
        850,
        790,
        790,
        0,
    )
    assert got["reconciliation"]["discrepancy"] == 60

    view = (await ai.get(f"/ai/read/shortages/{shortage.id}")).json()
    assert view["status"] == "PARTIALLY_RESOLVED"
    assert [s["id"] for s in view["shipments"]] == [str(po_delivered.id)]
    (residual,) = view["residual_shortages"]
    assert (residual["qty_required"], residual["shortfall"]) == (60, 60)

    r = await ai.get(f"/ai/read/shipments/{po_delivered.id}/coldchain")
    assert r.status_code == 200, r.text
    cold = r.json()
    assert cold["requires_cold_chain"] is False and cold["reading_count"] == 0
    assert cold["events"] == [] and cold["has_open_excursion"] is False

    # Hospital B is not involved in Supplier Y's shipment.
    b = await ai_client_for(world.users["b.APPROVER"])
    for url in (
        f"/ai/read/shipments/{po_delivered.id}",
        f"/ai/read/shipments/{po_delivered.id}/coldchain",
    ):
        assert (await b.get(url)).status_code == 403, url

    # Supplier Y sent it, so it sees the shipment, but not Hospital A's receipt (rule 6).
    desk_user = await session.scalar(select(User).where(User.email == "d@y.test"))
    assert desk_user is not None
    desk = await ai_client_for(desk_user)
    r = await desk.get(f"/ai/read/shipments/{po_delivered.id}")
    assert r.status_code == 200 and r.json()["receipt"] is None
