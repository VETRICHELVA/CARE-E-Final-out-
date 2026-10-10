"""The hub's database role (S20, CLAUDE.md rule 5: the audit log is append-only).

The audit_log trigger rejects UPDATE, DELETE and TRUNCATE, but the owner of a table (or a
superuser) can disable a trigger. So outside dev the hub and worker connect as `care_app`
(migration 0017_s20fix): ordinary DML on the app tables, no UPDATE, DELETE or TRUNCATE on the
append-only tables, and no ownership. Migrations keep an owner URL (MIGRATION_DATABASE_URL).

`require_least_privilege` runs when the hub or worker starts outside dev and refuses a
connection that could rewrite the audit log."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.config import settings

APP_ROLE = "care_app"
# Append-only tables (database triggers): the audit log and the credit ledger (S19).
APPEND_ONLY_TABLES = ("audit_log", "credit_ledger")

_PRIVILEGES = text(
    """
    SELECT current_user,
           r.rolsuper,
           pg_has_role(current_user, c.relowner, 'MEMBER') AS owner,
           has_table_privilege(current_user, c.oid, 'UPDATE')
             OR has_table_privilege(current_user, c.oid, 'DELETE')
             OR has_table_privilege(current_user, c.oid, 'TRUNCATE') AS rewrite
    FROM pg_roles r CROSS JOIN pg_class c
    WHERE r.rolname = current_user AND c.oid = 'audit_log'::regclass
    """
)


async def audit_role_refusal(conn: AsyncConnection) -> str | None:
    """Why the role `conn` acts as could rewrite or unprotect audit_log, or None."""
    row = (await conn.execute(_PRIVILEGES)).one()
    user, superuser, owner, rewrite = row
    if superuser:
        return f"{user} is a superuser"
    if owner:
        return f"{user} owns audit_log (or is a member of its owner) and could disable its trigger"
    if rewrite:
        return f"{user} may UPDATE, DELETE or TRUNCATE audit_log"
    return None


async def require_least_privilege(engine: AsyncEngine) -> None:
    """Outside dev, refuse to run with a DATABASE_URL role that could rewrite the audit log."""
    if settings.is_dev:
        return
    async with engine.connect() as conn:
        refusal = await audit_role_refusal(conn)
    if refusal is not None:
        raise RuntimeError(
            f"DATABASE_URL connects as a role that is too powerful: {refusal}. Outside "
            f"APP_ENV=dev the hub and worker connect as `{APP_ROLE}` (README, Configuration); "
            "migrations use MIGRATION_DATABASE_URL."
        )
