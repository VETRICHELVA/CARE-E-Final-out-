import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit
from app.audit.models import AuditLog
from app.conftest import ClientFor, World, as_owner

pytestmark = pytest.mark.anyio


async def test_user_reason_is_recorded_as_user(session: AsyncSession, world: World) -> None:
    actor = world.users["a.APPROVER"]
    entity_id = uuid.uuid4()
    row = await audit.record(
        session,
        actor,
        "shortage",
        entity_id,
        "shortage.created",
        None,
        {"qty": 5, "id": entity_id},
        "  Ward 3 ran out  ",
    )
    assert (row.reason, row.reason_source) == ("Ward 3 ran out", "USER")
    assert (row.actor_id, row.org_id) == (actor.id, world.hospital_a.id)
    assert row.after == {"qty": 5, "id": str(entity_id)}  # JSON-safe
    assert row.ts is not None


@pytest.mark.parametrize("reason", [None, "", "   "])
async def test_blank_user_reason_is_system_no_reason(
    session: AsyncSession, world: World, reason: str | None
) -> None:
    row = await audit.record(
        session, world.users["a.APPROVER"], "x", uuid.uuid4(), "x.done", None, None, reason
    )
    assert (row.reason, row.reason_source) == ("No reason was entered.", "SYSTEM")


async def test_system_actor_records_its_cause(session: AsyncSession, world: World) -> None:
    row = await audit.record(
        session,
        None,
        "source_request",
        uuid.uuid4(),
        "source_request.expired",
        {"status": "PENDING"},
        {"status": "EXPIRED"},
        "Response deadline passed.",
        org_id=world.hospital_b.id,
    )
    assert (row.actor_id, row.org_id) == (None, world.hospital_b.id)
    assert (row.reason, row.reason_source) == ("Response deadline passed.", "SYSTEM")


@pytest.mark.parametrize(("reason", "with_org"), [(None, True), ("  ", True), ("Cause.", False)])
async def test_system_actor_must_give_cause_and_org(
    session: AsyncSession, world: World, reason: str | None, with_org: bool
) -> None:
    org_id = world.hospital_a.id if with_org else None
    with pytest.raises(ValueError):
        await audit.record(
            session, None, "x", uuid.uuid4(), "x.done", None, None, reason, org_id=org_id
        )


@pytest.mark.parametrize(
    "sql",
    ["UPDATE audit_log SET reason = 'edited'", "DELETE FROM audit_log", "TRUNCATE audit_log"],
)
async def test_raw_sql_cannot_change_audit_rows(
    session: AsyncSession, world: World, sql: str
) -> None:
    await audit.record(
        session, world.users["a.APPROVER"], "x", uuid.uuid4(), "x.done", None, None, "r"
    )
    # The trigger stops even the owner (the hub's own role has no such privilege at all,
    # test_app_role.py).
    await as_owner(session)
    with pytest.raises(DBAPIError, match="audit_log is append-only"):
        async with session.begin_nested():
            await session.execute(text(sql))
    assert (await session.scalar(text("SELECT reason FROM audit_log"))) == "r"


async def _rows(session: AsyncSession, world: World) -> tuple[uuid.UUID, uuid.UUID]:
    """Two rows in A (same entity), one in B."""
    a_entity, b_entity = uuid.uuid4(), uuid.uuid4()
    a, b = world.users["a.STORE_MANAGER"], world.users["b.STORE_MANAGER"]
    await audit.record(session, a, "shortage", a_entity, "shortage.created", None, None, None)
    await audit.record(session, a, "shortage", a_entity, "shortage.cancelled", None, None, "dup")
    await audit.record(session, b, "shortage", b_entity, "shortage.created", None, None, None)
    return a_entity, b_entity


async def test_audit_lists_own_org_rows_newest_first(
    client_for: ClientFor, world: World, session: AsyncSession
) -> None:
    a_entity, _ = await _rows(session, world)
    client = await client_for(world.users["a.APPROVER"])
    r = await client.get("/audit")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["action"] for i in items] == ["shortage.cancelled", "shortage.created"]
    assert {i["org_id"] for i in items} == {str(world.hospital_a.id)}
    assert items[0]["reason_source"] == "USER"
    assert items[1]["reason"] == "No reason was entered."

    r = await client.get("/audit", params={"entity": "shortage", "entity_id": str(a_entity)})
    assert len(r.json()["items"]) == 2


async def test_audit_never_shows_another_orgs_rows(
    client_for: ClientFor, world: World, session: AsyncSession
) -> None:
    _, b_entity = await _rows(session, world)
    client = await client_for(world.users["a.APPROVER"])
    r = await client.get("/audit", params={"entity_id": str(b_entity)})
    assert r.status_code == 200
    assert r.json() == {"items": [], "next_cursor": None}


async def test_platform_admin_sees_all_orgs(
    client_for: ClientFor, world: World, session: AsyncSession
) -> None:
    await _rows(session, world)
    client = await client_for(world.users["p.ADMIN"])
    items = (await client.get("/audit")).json()["items"]
    assert {i["org_id"] for i in items} == {str(world.hospital_a.id), str(world.hospital_b.id)}


async def test_hospital_admin_sees_only_own_org(
    client_for: ClientFor, world: World, session: AsyncSession
) -> None:
    await _rows(session, world)
    client = await client_for(world.users["a.ADMIN"])
    items = (await client.get("/audit")).json()["items"]
    assert {i["org_id"] for i in items} == {str(world.hospital_a.id)}


async def test_audit_paginates(client_for: ClientFor, world: World, session: AsyncSession) -> None:
    await _rows(session, world)
    client = await client_for(world.users["p.ADMIN"])
    first = (await client.get("/audit", params={"limit": 2})).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = (
        await client.get("/audit", params={"limit": 2, "cursor": first["next_cursor"]})
    ).json()
    assert [i["action"] for i in second["items"]] == ["shortage.created"]
    assert second["next_cursor"] is None
    ids = [i["id"] for i in first["items"] + second["items"]]
    assert len(set(ids)) == 3 == await session.scalar(text("SELECT count(*) FROM audit_log"))


async def test_missing_capability_is_403(client_for: ClientFor, world: World) -> None:
    client = await client_for(world.users["a.REQUESTER"])
    r = await client.get("/audit")
    assert r.status_code == 403
    assert r.json() == {
        "code": "forbidden",
        "message": "Missing capability audit.read.",
        "details": {"capability": "audit.read"},
    }


async def test_entity_filter_with_nul_is_422(client_for: ClientFor, world: World) -> None:
    """Postgres text cannot hold NUL, so the filter is rejected before it reaches SQL."""
    client = await client_for(world.users["a.APPROVER"])
    r = await client.get("/audit", params={"entity": "inventory_batch\u0000"})
    assert (r.status_code, r.json()["code"]) == (422, "schema_error")


def test_audit_rows_have_no_update_columns() -> None:
    assert {"created_at", "updated_at"}.isdisjoint(AuditLog.__table__.columns.keys())
