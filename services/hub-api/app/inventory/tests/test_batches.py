import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.catalog.models import Product
from app.conftest import ClientFor, World
from app.db import flush_or_conflict
from app.errors import AppError
from app.inventory import service as inventory_service
from app.inventory.models import InventoryBatch, VerificationEvent
from app.inventory.schemas import CsvRow
from app.orgs.models import Facility, Organization

pytestmark = pytest.mark.anyio

TODAY = datetime.now(UTC).date()
INT4_MAX = 2_147_483_647
NUL = "\u0000"


async def facility_of(session: AsyncSession, org: Organization) -> Facility:
    facility = await session.scalar(select(Facility).where(Facility.org_id == org.id))
    assert facility is not None
    return facility


def scenario_b(product: Product, facility: Facility, **overrides: Any) -> dict[str, Any]:
    """Hospital B's Surgical Kit A batch from Scenario 1: 1,000 transferable."""
    return {
        "facility_id": str(facility.id),
        "product_id": str(product.id),
        "batch_no": "SKA-2026-01",
        "on_hand": 2500,
        "reserved": 800,
        "allocated": 200,
        "safety_stock": 500,
        "expiry_date": str(TODAY + timedelta(days=180)),
        "unit_cost_paise": 1500,
        **overrides,
    }


async def audit_rows(session: AsyncSession, entity_id: str) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.entity_id == uuid.UUID(entity_id)).order_by(AuditLog.ts)
    return list(await session.scalars(stmt))


@pytest.fixture
async def manager_a(client_for: ClientFor, world: World) -> httpx.AsyncClient:
    return await client_for(world.users["a.STORE_MANAGER"])


@pytest.fixture
async def batch_a(
    manager_a: httpx.AsyncClient, session: AsyncSession, world: World, products: dict[str, Product]
) -> dict[str, Any]:
    facility = await facility_of(session, world.hospital_a)
    r = await manager_a.post(
        "/inventory/batches", json=scenario_b(products["SURG-KIT-A"], facility)
    )
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


# --- create, list, update ----------------------------------------------------------------


async def test_create_returns_hub_computed_transferable(
    batch_a: dict[str, Any], world: World, session: AsyncSession
) -> None:
    assert batch_a["transferable"] == 1000
    assert batch_a["org_id"] == str(world.hospital_a.id)
    assert (batch_a["quarantined"], batch_a["last_verified_at"]) == (0, None)
    [row] = await audit_rows(session, batch_a["id"])
    assert (row.action, row.before, row.reason_source) == (
        "inventory_batch.created",
        None,
        "SYSTEM",
    )
    assert row.after is not None and row.after["on_hand"] == 2500


async def test_expired_batch_has_zero_transferable(
    manager_a: httpx.AsyncClient, session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    facility = await facility_of(session, world.hospital_a)
    body = scenario_b(products["SURG-KIT-A"], facility, expiry_date=str(TODAY))
    r = await manager_a.post("/inventory/batches", json=body)
    assert (r.status_code, r.json()["transferable"]) == (201, 0)


@pytest.mark.parametrize(
    "field", [{"transferable": 5000}, {"last_verified_at": "2026-10-05T00:00:00Z"}]
)
async def test_client_cannot_set_computed_fields(
    manager_a: httpx.AsyncClient,
    batch_a: dict[str, Any],
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    field: dict[str, Any],
) -> None:
    facility = await facility_of(session, world.hospital_a)
    body = scenario_b(products["SURG-KIT-A"], facility, batch_no="X-1", **field)
    created = await manager_a.post("/inventory/batches", json=body)
    patched = await manager_a.patch(f"/inventory/batches/{batch_a['id']}", json=field)
    assert (created.status_code, created.json()["code"]) == (422, "schema_error")
    assert (patched.status_code, patched.json()["code"]) == (422, "schema_error")


async def test_patch_recomputes_transferable_and_audits(
    manager_a: httpx.AsyncClient, batch_a: dict[str, Any], session: AsyncSession
) -> None:
    url = f"/inventory/batches/{batch_a['id']}"
    r = await manager_a.patch(url, json={"quarantined": 100, "reason": "Damaged cartons"})
    assert (r.status_code, r.json()["quarantined"], r.json()["transferable"]) == (200, 100, 900)
    assert r.json()["updated_at"] > batch_a["updated_at"]

    # Same values again: no state change, so no audit row.
    assert (await manager_a.patch(url, json={"quarantined": 100})).status_code == 200
    rows = await audit_rows(session, batch_a["id"])
    assert [r.action for r in rows] == ["inventory_batch.created", "inventory_batch.updated"]
    assert (rows[1].before, rows[1].after) == ({"quarantined": 0}, {"quarantined": 100})
    assert (rows[1].reason, rows[1].reason_source) == ("Damaged cartons", "USER")


async def test_list_shows_own_org_batches_only(
    client_for: ClientFor, world: World, batch_a: dict[str, Any]
) -> None:
    own = await (await client_for(world.users["a.REQUESTER"])).get("/inventory/batches")
    assert [(b["id"], b["transferable"]) for b in own.json()["items"]] == [(batch_a["id"], 1000)]
    for user in ("b.STORE_MANAGER", "s.SUPPLIER_DESK"):
        other = await (await client_for(world.users[user])).get("/inventory/batches")
        assert (other.status_code, other.json()["items"]) == (200, [])


async def test_duplicate_batch_is_a_conflict(
    manager_a: httpx.AsyncClient,
    batch_a: dict[str, Any],
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    facility = await facility_of(session, world.hospital_a)
    body = scenario_b(products["SURG-KIT-A"], facility)
    r = await manager_a.post("/inventory/batches", json=body)
    assert (r.status_code, r.json()["code"]) == (409, "conflict")

    other = await manager_a.post("/inventory/batches", json={**body, "batch_no": "SKA-2026-02"})
    clash = await manager_a.patch(
        f"/inventory/batches/{other.json()['id']}", json={"batch_no": "SKA-2026-01"}
    )
    assert (clash.status_code, clash.json()["code"]) == (409, "conflict")


async def test_unknown_references(
    manager_a: httpx.AsyncClient, session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    facility = await facility_of(session, world.hospital_a)
    body = scenario_b(products["SURG-KIT-A"], facility, product_id=str(uuid.uuid4()))
    assert (await manager_a.post("/inventory/batches", json=body)).status_code == 400
    missing = await manager_a.patch(f"/inventory/batches/{uuid.uuid4()}", json={"on_hand": 1})
    assert missing.status_code == 404


# --- permissions ---------------------------------------------------------------------------


async def test_another_org_cannot_edit_or_verify(
    client_for: ClientFor, world: World, batch_a: dict[str, Any], session: AsyncSession
) -> None:
    b = await client_for(world.users["b.STORE_MANAGER"])
    url = f"/inventory/batches/{batch_a['id']}"
    patched = await b.patch(url, json={"on_hand": 1})
    verified = await b.post(f"{url}/verify", json={"method": "MANUAL", "counted_qty": 1})
    assert (patched.status_code, patched.json()["code"]) == (403, "forbidden")
    assert (verified.status_code, verified.json()["code"]) == (403, "forbidden")
    batch = await session.get(InventoryBatch, uuid.UUID(batch_a["id"]))
    assert batch is not None and (batch.on_hand, batch.last_verified_at) == (2500, None)


@pytest.mark.parametrize("user", ["s.SUPPLIER_DESK", "s.ADMIN", "p.ADMIN", "a.REQUESTER"])
async def test_only_hospital_inventory_editors_create_batches(
    client_for: ClientFor,
    world: World,
    session: AsyncSession,
    products: dict[str, Product],
    user: str,
) -> None:
    """Supplier and platform users are refused even as ADMIN (HOSPITAL orgs only);
    a hospital REQUESTER lacks `inventory.edit`."""
    org = world.users[user].org
    facility = await facility_of(session, org)
    client = await client_for(world.users[user])
    r = await client.post("/inventory/batches", json=scenario_b(products["SURG-KIT-A"], facility))
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")


async def test_cannot_create_in_another_orgs_facility(
    manager_a: httpx.AsyncClient, session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    facility_b = await facility_of(session, world.hospital_b)
    r = await manager_a.post(
        "/inventory/batches", json=scenario_b(products["SURG-KIT-A"], facility_b)
    )
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")


# --- verify --------------------------------------------------------------------------------


async def test_verify_records_event_and_last_verified_at(
    manager_a: httpx.AsyncClient, batch_a: dict[str, Any], session: AsyncSession, world: World
) -> None:
    url = f"/inventory/batches/{batch_a['id']}/verify"
    same = await manager_a.post(url, json={"method": "MANUAL", "counted_qty": 2500})
    assert same.status_code == 200
    assert (same.json()["on_hand"], same.json()["transferable"]) == (2500, 1000)
    assert same.json()["last_verified_at"] is not None

    recount = await manager_a.post(url, json={"counted_qty": 2400, "method": "SCAN"})
    assert (recount.json()["on_hand"], recount.json()["transferable"]) == (2400, 900)
    assert recount.json()["last_verified_at"] > same.json()["last_verified_at"]

    events = list(
        await session.scalars(
            select(VerificationEvent)
            .where(VerificationEvent.batch_id == uuid.UUID(batch_a["id"]))
            .order_by(VerificationEvent.ts)
        )
    )
    assert [(e.method, e.counted_qty) for e in events] == [("MANUAL", 2500), ("SCAN", 2400)]
    assert all(e.user_id == world.users["a.STORE_MANAGER"].id for e in events)
    assert recount.json()["last_verified_at"] == events[1].ts.isoformat().replace("+00:00", "Z")

    rows = await audit_rows(session, batch_a["id"])
    verified = [r for r in rows if r.action == "inventory_batch.verified"]
    assert len(verified) == 2
    assert verified[1].before is not None and verified[1].before["on_hand"] == 2500
    assert verified[1].after is not None and verified[1].after["on_hand"] == 2400
    assert verified[1].after["verification_event_id"] == str(events[1].id)


async def test_verify_needs_inventory_edit(
    client_for: ClientFor, world: World, batch_a: dict[str, Any]
) -> None:
    client = await client_for(world.users["a.REQUESTER"])
    body = {"method": "MANUAL", "counted_qty": 1}
    r = await client.post(f"/inventory/batches/{batch_a['id']}/verify", json=body)
    assert r.status_code == 403


async def test_verify_requires_a_method(
    manager_a: httpx.AsyncClient, batch_a: dict[str, Any]
) -> None:
    """A count is recorded only with the method the user stated (no assumed MANUAL)."""
    url = f"/inventory/batches/{batch_a['id']}/verify"
    r = await manager_a.post(url, json={"counted_qty": 2500})
    assert (r.status_code, r.json()["code"]) == (422, "schema_error")
    assert r.json()["details"]["errors"][0]["loc"] == ["body", "method"]


# --- CSV import ----------------------------------------------------------------------------

EXP = str(TODAY + timedelta(days=200))
HEADER = "product_code,batch_no,on_hand,reserved,allocated,safety_stock,quarantined,expiry_date,"
CSV_5_VALID_2_INVALID = f"""{HEADER}unit_cost_paise
SURG-KIT-A,K-1,1400,800,,500,,{EXP},1500
IV-CAN-20G,C-1,1000,,,200,,{EXP},900
NO-SUCH-CODE,X-1,10,,,,,{EXP},100
diag-rdk,R-1,500,,,,,{EXP},25000
PPE-N95,N-1,300,0,0,0,0,{EXP},4000
SURG-KIT-A,K-2,-5,,,,,{EXP},1500
WND-GZE-STR,G-1,50,,,,,{EXP},200
"""


async def import_csv(
    client: httpx.AsyncClient, facility: Facility, text: str | bytes, reason: str | None = None
) -> httpx.Response:
    params = {"facility_id": str(facility.id)} | ({"reason": reason} if reason else {})
    return await client.post(
        "/inventory/batches/import",
        params=params,
        content=text.encode() if isinstance(text, str) else text,
        headers={"Content-Type": "text/csv"},
    )


async def test_import_inserts_valid_rows_and_reports_invalid_ones(
    manager_a: httpx.AsyncClient, session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    facility = await facility_of(session, world.hospital_a)
    r = await import_csv(manager_a, facility, CSV_5_VALID_2_INVALID)
    assert r.status_code == 200
    assert r.json()["inserted"] == 5
    errors = r.json()["errors"]
    assert [e["line"] for e in errors] == [4, 7]
    assert errors[0]["message"] == "Unknown product code NO-SUCH-CODE."
    assert "on_hand" in errors[1]["message"]

    listed = (await manager_a.get("/inventory/batches")).json()["items"]
    by_no = {b["batch_no"]: b for b in listed}
    assert set(by_no) == {"K-1", "C-1", "R-1", "N-1", "G-1"}
    assert by_no["K-1"]["transferable"] == 100  # Hospital C's Scenario 1 numbers
    assert by_no["R-1"]["product_id"] == str(products["DIAG-RDK"].id)
    actions = await session.scalars(
        select(AuditLog.action).where(AuditLog.org_id == world.hospital_a.id)
    )
    assert list(actions) == ["inventory_batch.imported"] * 5


async def test_reimport_reports_duplicates_instead_of_inserting(
    manager_a: httpx.AsyncClient, session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    facility = await facility_of(session, world.hospital_a)
    csv_text = f"product_code,batch_no,on_hand,expiry_date,unit_cost_paise\nPPE-N95,N-1,3,{EXP},1\n"
    assert (await import_csv(manager_a, facility, csv_text)).json()["inserted"] == 1
    again = (await import_csv(manager_a, facility, csv_text + f"PPE-N95,N-1,4,{EXP},1\n")).json()
    assert again["inserted"] == 0
    assert [e["line"] for e in again["errors"]] == [2, 3]
    assert "already exists" in again["errors"][0]["message"]


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("product_code,batch_no,expiry_date,unit_cost_paise\n", "missing"),
        ("product_code,batch_no,on_hand,expiry_date,unit_cost_paise,transferable\n", "unknown"),
        ("", "missing"),
    ],
)
async def test_import_rejects_bad_headers(
    manager_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    text: str,
    key: str,
) -> None:
    facility = await facility_of(session, world.hospital_a)
    r = await import_csv(manager_a, facility, text)
    assert (r.status_code, r.json()["code"]) == (400, "validation")
    assert key in r.json()["details"]


async def test_import_rejects_unreadable_or_large_files(
    manager_a: httpx.AsyncClient, session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    facility = await facility_of(session, world.hospital_a)
    assert (await import_csv(manager_a, facility, b"\xff\xfe\x00bad")).status_code == 400
    assert (await import_csv(manager_a, facility, b"x" * 1_000_001)).status_code == 400


@pytest.mark.parametrize("user", ["b.STORE_MANAGER", "s.ADMIN", "a.REQUESTER"])
async def test_import_permissions(
    client_for: ClientFor, world: World, session: AsyncSession, user: str
) -> None:
    """B cannot import into A's facility; a supplier ADMIN cannot import even into its
    own org's facility (HOSPITAL orgs only); a REQUESTER lacks `inventory.edit`."""
    owner = world.users["s.ADMIN"].org if user == "s.ADMIN" else world.hospital_a
    facility = await facility_of(session, owner)
    r = await import_csv(await client_for(world.users[user]), facility, CSV_5_VALID_2_INVALID)
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")


async def test_openapi_marks_transferable_read_only_and_import_as_csv(
    client_for: ClientFor,
) -> None:
    spec = (await (await client_for()).get("/openapi.json")).json()
    schemas = spec["components"]["schemas"]
    assert schemas["BatchOut"]["properties"]["transferable"]["readOnly"] is True
    assert "transferable" not in schemas["BatchCreate"]["properties"]
    assert "transferable" not in schemas["BatchUpdate"]["properties"]
    body = spec["paths"]["/api/v1/inventory/batches/import"]["post"]["requestBody"]
    assert list(body["content"]) == ["text/csv"]


# --- values the database cannot store ------------------------------------------------------


async def test_quantities_and_paise_must_fit_int4(
    manager_a: httpx.AsyncClient,
    batch_a: dict[str, Any],
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    """int4 columns: past 2,147,483,647 is a 422, never a 500."""
    facility = await facility_of(session, world.hospital_a)
    at_max = scenario_b(products["SURG-KIT-A"], facility, batch_no="MAX", on_hand=INT4_MAX)
    assert (await manager_a.post("/inventory/batches", json=at_max)).status_code == 201
    url = f"/inventory/batches/{batch_a['id']}"
    too_big = INT4_MAX + 1
    for method, path, body in [
        ("POST", "/inventory/batches", {**at_max, "batch_no": "BIG", "on_hand": too_big}),
        ("POST", "/inventory/batches", {**at_max, "batch_no": "BIG", "unit_cost_paise": too_big}),
        ("PATCH", url, {"reserved": too_big}),
        ("POST", f"{url}/verify", {"method": "MANUAL", "counted_qty": too_big}),
    ]:
        r = await manager_a.request(method, path, json=body)
        assert (r.status_code, r.json()["code"]) == (422, "schema_error"), (path, body)


async def test_text_with_nul_is_rejected(
    manager_a: httpx.AsyncClient,
    batch_a: dict[str, Any],
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
) -> None:
    """Postgres text cannot hold NUL: batch_no and every reason are a 422, never a 500."""
    facility = await facility_of(session, world.hospital_a)
    body = scenario_b(products["SURG-KIT-A"], facility, batch_no="NEW-1")
    url = f"/inventory/batches/{batch_a['id']}"
    for method, path, payload in [
        ("POST", "/inventory/batches", {**body, "batch_no": f"A{NUL}B"}),
        ("POST", "/inventory/batches", {**body, "reason": f"r{NUL}"}),
        ("PATCH", url, {"batch_no": f"A{NUL}B"}),
        ("PATCH", url, {"on_hand": 1, "reason": f"r{NUL}"}),
        ("POST", f"{url}/verify", {"method": "MANUAL", "counted_qty": 1, "reason": f"r{NUL}"}),
    ]:
        r = await manager_a.request(method, path, json=payload)
        assert (r.status_code, r.json()["code"]) == (422, "schema_error"), payload
    csv_text = f"product_code,batch_no,on_hand,expiry_date,unit_cost_paise\nPPE-N95,R-1,1,{EXP},1\n"
    r = await import_csv(manager_a, facility, csv_text, reason=f"r{NUL}")
    assert (r.status_code, r.json()["code"]) == (422, "schema_error")


async def test_import_reports_out_of_range_and_nul_values_per_row(
    manager_a: httpx.AsyncClient, session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    facility = await facility_of(session, world.hospital_a)
    csv_text = (
        "product_code,batch_no,on_hand,expiry_date,unit_cost_paise\n"
        f"PPE-N95,OK-1,5,{EXP},1\n"
        f"PPE-N95,BIG-1,{INT4_MAX + 1},{EXP},1\n"
        f"PPE-N95,N{NUL}UL,5,{EXP},1\n"
        f"PPE{NUL}N95,OK-X,5,{EXP},1\n"
        f"PPE-N95,OK-2,5,{EXP},1\n"
    )
    r = await import_csv(manager_a, facility, csv_text)
    assert (r.status_code, r.json()["inserted"]) == (200, 2)
    errors = [(e["line"], e["message"].split(":")[0]) for e in r.json()["errors"]]
    assert errors == [(3, "on_hand"), (4, "batch_no"), (5, "product_code")]
    listed = (await manager_a.get("/inventory/batches")).json()["items"]
    assert sorted(b["batch_no"] for b in listed) == ["OK-1", "OK-2"]


async def test_import_reports_database_data_errors_per_row(
    manager_a: httpx.AsyncClient,
    session: AsyncSession,
    world: World,
    products: dict[str, Product],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Safety net: with the schema checks bypassed, values Postgres rejects (an int4
    overflow, a NUL) still become row errors, and the other rows are inserted."""

    class Unchecked(CsvRow):
        batch_no: str
        on_hand: int

    monkeypatch.setattr(inventory_service, "CsvRow", Unchecked)
    facility = await facility_of(session, world.hospital_a)
    csv_text = (
        "product_code,batch_no,on_hand,expiry_date,unit_cost_paise\n"
        f"PPE-N95,OK-1,5,{EXP},1\n"
        f"PPE-N95,BIG-1,{INT4_MAX + 1},{EXP},1\n"
        f"PPE-N95,N{NUL}UL,5,{EXP},1\n"
        f"PPE-N95,OK-2,5,{EXP},1\n"
    )
    r = await import_csv(manager_a, facility, csv_text)
    assert (r.status_code, r.json()["inserted"]) == (200, 2)
    assert [e["line"] for e in r.json()["errors"]] == [3, 4]
    listed = (await manager_a.get("/inventory/batches")).json()["items"]
    assert sorted(b["batch_no"] for b in listed) == ["OK-1", "OK-2"]


async def test_only_unique_violations_become_conflicts(
    session: AsyncSession, world: World, products: dict[str, Product]
) -> None:
    """A duplicate is 409 `conflict`; any other integrity error (here a missing product)
    is a bug and is re-raised, not reported as "already exists"."""
    facility = await facility_of(session, world.hospital_a)

    def batch(batch_no: str, product_id: uuid.UUID) -> InventoryBatch:
        return InventoryBatch(
            org_id=world.hospital_a.id,
            facility_id=facility.id,
            product_id=product_id,
            batch_no=batch_no,
            on_hand=1,
            expiry_date=TODAY + timedelta(days=200),
            unit_cost_paise=1,
        )

    n95 = products["PPE-N95"].id
    await flush_or_conflict(session, batch("U-1", n95), "Duplicate.")
    with pytest.raises(AppError) as duplicate:
        await flush_or_conflict(session, batch("U-1", n95), "Duplicate.")
    assert (duplicate.value.status, duplicate.value.code) == (409, "conflict")
    with pytest.raises(IntegrityError):
        await flush_or_conflict(session, batch("U-2", uuid.uuid4()), "Duplicate.")
    # The failed savepoints were rolled back, so the session is still usable.
    assert await session.scalar(select(func.count()).select_from(InventoryBatch)) == 1
