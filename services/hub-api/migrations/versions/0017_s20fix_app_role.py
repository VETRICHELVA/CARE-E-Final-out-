"""S20 fix: the least-privilege role `care_app` the hub and worker connect as outside dev

Revision ID: 0017_s20fix
Revises: 0016_s20
Create Date: 2026-10-09 18:00:00.000000

The audit_log trigger rejects UPDATE, DELETE and TRUNCATE, but the owner of the table can
disable the trigger. `care_app` owns nothing: it gets SELECT, INSERT, UPDATE and DELETE on the
app tables (and on every table later migrations create, through default privileges), minus
UPDATE and DELETE on the append-only tables, and never TRUNCATE, REFERENCES or TRIGGER.

The role is created NOLOGIN, cluster-wide, if it does not exist yet; it works the same with
`make up` (Docker, where `care` is the superuser) and any Postgres where the migrating role may
create roles. Outside dev, give it a login, e.g. `ALTER ROLE care_app LOGIN PASSWORD '...'`,
and point DATABASE_URL at it (README, Configuration). Migrations keep running as the owner.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017_s20fix"
down_revision: str | Sequence[str] | None = "0016_s20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "care_app"
APPEND_ONLY = ("audit_log", "credit_ledger")
DML = "SELECT, INSERT, UPDATE, DELETE"


def upgrade() -> None:
    # Roles are cluster-wide: another database (or a parallel test run) may have made it.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} NOLOGIN;
            END IF;
        EXCEPTION WHEN duplicate_object OR unique_violation THEN
            NULL;
        END
        $$
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    op.execute(f"GRANT {DML} ON ALL TABLES IN SCHEMA public TO {ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {ROLE}")
    # Tables and sequences later migrations create (as this same owner) get the same grants.
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {DML} ON TABLES TO {ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {ROLE}"
    )
    for table in APPEND_ONLY:
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM {ROLE}")
    # The schema version is the migrations' business.
    op.execute(f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON alembic_version FROM {ROLE}")


def downgrade() -> None:
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {ROLE}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {ROLE}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ROLE}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
    # The role itself stays: other databases in the cluster may still grant it privileges.
