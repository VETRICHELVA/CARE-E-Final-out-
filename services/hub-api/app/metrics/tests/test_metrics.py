"""GET /metrics/network (S20): platform users with audit.read only; every figure from
recorded rows (api-and-events.md "Network metrics")."""

from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.coldchain.models import ColdChainEvent
from app.conftest import ClientFor, World
from app.iot.models import Device, SensorReading
from app.receiving.tests.conftest import receipt
from app.shipments.models import Shipment
from app.shipments.tests.conftest import Fleet
from app.shortages.models import Candidate, SourceType
from app.source_requests.models import Hold, SourceRequest
from app.surplus.models import SurplusPost

pytestmark = pytest.mark.anyio


async def metrics(client_for: ClientFor, world: World) -> dict[str, Any]:
    r = await (await client_for(world.users["p.ADMIN"])).get("/metrics/network")
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def test_an_empty_network_has_zeros_and_no_rates(world: World, client_for: ClientFor) -> None:
    body = await metrics(client_for, world)
    assert body["time_to_confirmed_source"] == {
        "median_minutes": None, "shortages_confirmed": 0, "shortages_reported": 0,
    }  # fmt: skip
    assert body["resolution_mix"] == {
        "transfers": 0, "purchases": 0, "transfer_share": None, "purchase_share": None,
    }  # fmt: skip
    assert body["procurement_cost_avoided"] == {"paise": 0, "units_priced": 0, "units_unpriced": 0}
    assert body["units_saved_from_expiry"] == {"units": 0}
    assert body["cold_chain"]["compliance_rate"] is None


@pytest.mark.parametrize("user", ["a.APPROVER", "a.ADMIN", "s.ADMIN", "a.REQUESTER"])
async def test_only_the_platform_reads_network_metrics(
    world: World, client_for: ClientFor, user: str
) -> None:
    """An org's own audit.read does not open network-wide figures."""
    r = await (await client_for(world.users[user])).get("/metrics/network")
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "forbidden"


async def test_unauthenticated_is_401(client_for: ClientFor) -> None:
    assert (await (await client_for()).get("/metrics/network")).status_code == 401


async def test_a_received_transfer_from_posted_surplus(
    session: AsyncSession,
    world: World,
    client_for: ClientFor,
    delivered: Shipment,
    receiver: httpx.AsyncClient,
    swiftmed: Fleet,
) -> None:
    """Scenario 1 with B accepting: 850 transferred, 805 accepted. B had posted 300 of the
    held batch as surplus before the request; the box saw one excursion."""
    hold = await session.scalar(
        select(Hold).where(Hold.source_request_id == delivered.source_request_id)
    )
    assert hold is not None and hold.qty == 850
    session.add(
        SurplusPost(
            org_id=world.hospital_b.id,
            batch_id=hold.batch_id,
            product_id=delivered.product_id,
            qty=300,
            expiry_date=(hold.created_at + timedelta(days=55)).date(),
            status="MATCHED",
            created_by=world.users["b.STORE_MANAGER"].id,
            created_at=hold.created_at - timedelta(days=1),
        )
    )
    # A cold-chain delivery with readings and an excursion.
    delivered.requires_cold_chain = True
    session.add(Device(org_id=swiftmed.org.id, device_id="cb-metrics"))
    await session.flush()
    session.add(SensorReading(device_id="cb-metrics", ts=hold.created_at, temp_c=9.4,
                              battery=90, shipment_id=delivered.id))  # fmt: skip
    session.add(ColdChainEvent(shipment_id=delivered.id, device_id="cb-metrics", type="EXCURSION",
                               threshold=8.0, observed_value=9.4, severity="ALERT",
                               ts=hold.created_at))  # fmt: skip
    await session.flush()

    r = await receiver.post(
        f"/shipments/{delivered.id}/receipt",
        json=receipt(850, 805, 45, condition="DAMAGED", inspection_note="Crushed cartons."),
    )
    assert r.status_code in (200, 201), r.text

    body = await metrics(client_for, world)
    t = body["time_to_confirmed_source"]
    # The residual of 45 is a second reported shortage, not yet sourced.
    assert (t["shortages_confirmed"], t["shortages_reported"]) == (1, 2)
    assert t["median_minutes"] is not None and t["median_minutes"] >= 0
    assert body["resolution_mix"] == {
        "transfers": 1, "purchases": 0, "transfer_share": 1.0, "purchase_share": 0.0,
    }  # fmt: skip
    # 805 accepted x Supplier X's 14.00, the cheapest supplier price in B's match run.
    assert body["procurement_cost_avoided"] == {
        "paise": 805 * 1400, "units_priced": 805, "units_unpriced": 0,
    }  # fmt: skip
    assert body["units_saved_from_expiry"] == {"units": 300}
    assert body["cold_chain"] == {
        "deliveries": 1, "monitored": 1, "with_excursion": 1, "with_device_silent": 0,
        "compliance_rate": 0.0,
    }  # fmt: skip
    assert "unit_cost" not in r.text and "1500" not in str(body)


async def supplier_candidates(session: AsyncSession, delivered: Shipment) -> list[Candidate]:
    """The supplier candidates of the match run that chose the transfer's source."""
    request = await session.get(SourceRequest, delivered.source_request_id)
    assert request is not None
    chosen = await session.get(Candidate, request.candidate_id)
    assert chosen is not None
    return list(
        await session.scalars(
            select(Candidate).where(
                Candidate.match_run_id == chosen.match_run_id,
                Candidate.source_type == SourceType.SUPPLIER,
            )
        )
    )


async def test_cost_avoided_ignores_a_cheaper_supplier_the_run_rejected(
    session: AsyncSession,
    world: World,
    client_for: ClientFor,
    delivered: Shipment,
    receiver: httpx.AsyncClient,
) -> None:
    """A supplier at 9.00 that failed a gate could not have been bought from: the 805 units
    are priced at the cheapest eligible supplier, X at 14.00."""
    suppliers = await supplier_candidates(session, delivered)
    assert {(c.eligible, c.unit_price_paise) for c in suppliers} >= {(True, 1400)}
    cheaper = suppliers[0]
    session.add(
        Candidate(
            match_run_id=cheaper.match_run_id,
            source_org_id=world.supplier.id,
            source_type=SourceType.SUPPLIER,
            offered_qty=5000,
            unit_price_paise=900,
            gate_results=[{"gate": "authorization", "passed": False, "reason": "Not authorized"}],
            eligible=False,
            eta_hours=10.0,
            reliability=100,
        )
    )
    await session.flush()
    r = await receiver.post(f"/shipments/{delivered.id}/receipt", json=receipt(850, 805, 45))
    assert r.status_code in (200, 201), r.text
    body = await metrics(client_for, world)
    assert body["procurement_cost_avoided"] == {
        "paise": 805 * 1400, "units_priced": 805, "units_unpriced": 0,
    }  # fmt: skip


async def test_a_transfer_with_no_eligible_supplier_is_not_counted(
    session: AsyncSession,
    world: World,
    client_for: ClientFor,
    delivered: Shipment,
    receiver: httpx.AsyncClient,
) -> None:
    """Every supplier in the run failed a gate: no purchase was possible, so the transfer
    avoided no recorded purchase price and its units are unpriced, never guessed."""
    suppliers = await supplier_candidates(session, delivered)
    assert suppliers and all(c.unit_price_paise is not None for c in suppliers)
    for c in suppliers:
        c.eligible, c.rank = False, None
    await session.flush()
    r = await receiver.post(f"/shipments/{delivered.id}/receipt", json=receipt(850, 805, 45))
    assert r.status_code in (200, 201), r.text
    body = await metrics(client_for, world)
    assert body["procurement_cost_avoided"] == {
        "paise": 0,
        "units_priced": 0,
        "units_unpriced": 805,
    }


async def test_a_purchase_counts_as_a_purchase_and_avoids_nothing(
    world: World,
    client_for: ClientFor,
    po_delivered: Shipment,
    receiver: httpx.AsyncClient,
) -> None:
    """Scenario 1 as in the demo: B declines, Supplier Y's PO is acknowledged (the confirmed
    source), 790 accepted."""
    r = await receiver.post(f"/shipments/{po_delivered.id}/receipt", json=receipt(790, 790))
    assert r.status_code in (200, 201), r.text
    body = await metrics(client_for, world)
    t = body["time_to_confirmed_source"]
    # The residual of 60 is a second reported shortage, not yet sourced.
    assert (t["shortages_confirmed"], t["shortages_reported"]) == (1, 2)
    assert body["resolution_mix"]["purchases"] == 1
    assert body["resolution_mix"]["transfers"] == 0
    assert body["procurement_cost_avoided"]["paise"] == 0
    assert body["units_saved_from_expiry"] == {"units": 0}
    assert body["cold_chain"]["deliveries"] == 0
