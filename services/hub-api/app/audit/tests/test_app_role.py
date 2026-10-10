"""S20: the least-privilege role `care_app` (migration 0017_s20fix) that the hub and worker
connect as outside dev. It does ordinary DML but cannot rewrite the audit log or switch off
its trigger; the hub refuses to start outside dev as a role that could."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app import dbrole
from app.audit import service as audit
from app.config import settings
from app.conftest import TEST_DB_URL, World, as_owner
from app.orgs.models import OrgStatus

pytestmark = pytest.mark.anyio

INSUFFICIENT_PRIVILEGE = "42501"


async def as_app_role(session: AsyncSession) -> None:
    """The rest of this test's transaction runs with care_app's privileges."""
    await session.execute(text(f"SET LOCAL ROLE {dbrole.APP_ROLE}"))
    assert await session.scalar(text("SELECT current_user")) == dbrole.APP_ROLE


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_log SET reason = 'edited'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
        "ALTER TABLE audit_log DISABLE TRIGGER USER",
        "ALTER TABLE audit_log DISABLE TRIGGER ALL",
        "DROP TRIGGER audit_log_no_update_delete ON audit_log",
        "ALTER TABLE audit_log OWNER TO care_app",
        "SET session_replication_role = replica",  # would skip ordinary triggers
        "UPDATE credit_ledger SET delta = 1",
        "DELETE FROM credit_ledger",
        "TRUNCATE credit_ledger",
        "ALTER TABLE credit_ledger DISABLE TRIGGER ALL",
    ],
)
async def test_the_app_role_cannot_rewrite_or_unprotect_the_audit_log(
    session: AsyncSession, world: World, sql: str
) -> None:
    await audit.record(
        session, world.users["a.APPROVER"], "x", uuid.uuid4(), "x.done", None, None, "kept"
    )
    await as_app_role(session)
    with pytest.raises(DBAPIError) as e:
        async with session.begin_nested():
            await session.execute(text(sql))
    assert getattr(e.value.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE, e.value
    assert await session.scalar(text("SELECT reason FROM audit_log")) == "kept"


async def test_the_app_role_does_the_hubs_ordinary_work(
    session: AsyncSession, world: World
) -> None:
    """Reads, inserts (audit rows included), updates and deletes on ordinary tables."""
    await as_app_role(session)
    row = await audit.record(
        session, world.users["a.APPROVER"], "x", uuid.uuid4(), "x.done", None, None, "r"
    )
    assert await session.scalar(text("SELECT count(*) FROM audit_log WHERE id = :id"),
                                {"id": row.id}) == 1  # fmt: skip
    world.hospital_b.status = OrgStatus.SUSPENDED
    await session.flush()
    await session.execute(text("DELETE FROM notification"))
    assert await dbrole.audit_role_refusal(await session.connection()) is None


async def test_the_owner_is_refused_and_the_app_role_is_not(session: AsyncSession) -> None:
    """The start-up check (outside dev) refuses the tests' owner role `care`, a superuser."""
    await as_owner(session)
    refusal = await dbrole.audit_role_refusal(await session.connection())
    assert refusal is not None and "superuser" in refusal
    await as_app_role(session)
    assert await dbrole.audit_role_refusal(await session.connection()) is None


@pytest.mark.parametrize(
    "grant",
    ["UPDATE ON credit_ledger", "DELETE ON credit_ledger", "UPDATE (reason) ON audit_log"],
)
async def test_a_role_that_could_rewrite_any_append_only_table_is_refused(
    session: AsyncSession, grant: str
) -> None:
    """Every append-only table is checked, column-level UPDATE grants included."""
    await as_owner(session)
    await session.execute(text(f"GRANT {grant} TO {dbrole.APP_ROLE}"))
    await as_app_role(session)
    refusal = await dbrole.audit_role_refusal(await session.connection())
    assert refusal is not None and grant.rsplit(" ", 1)[-1] in refusal


async def test_outside_dev_the_hub_will_not_start_as_the_owner(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    try:
        await dbrole.require_least_privilege(engine)  # dev: not checked
        monkeypatch.setattr(settings, "app_env", "production")
        with pytest.raises(RuntimeError, match="too powerful"):
            await dbrole.require_least_privilege(engine)
    finally:
        await engine.dispose()
    # Connected as care_app (the `role` startup setting stands in for its own login here).
    app_engine = create_async_engine(
        TEST_DB_URL,
        poolclass=NullPool,
        connect_args={"server_settings": {"role": dbrole.APP_ROLE}},
    )
    try:
        await dbrole.require_least_privilege(app_engine)
    finally:
        await app_engine.dispose()
