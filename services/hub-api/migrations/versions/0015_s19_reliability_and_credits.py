"""S19: reliability scores and the credit ledger

Revision ID: 0015_s19
Revises: 0014_s18
Create Date: 2026-10-08 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_s19"
down_revision: str | Sequence[str] | None = "0014_s18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Credits are a ledger: rows are append-only, as the audit log's are.
APPEND_ONLY_FN = """
CREATE FUNCTION credit_ledger_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'credit_ledger is append-only: % is not allowed', TG_OP;
END
$$
"""


def upgrade() -> None:
    op.create_table(
        "reliability_score",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("acceptance_rate", sa.Double(), nullable=True),
        sa.Column("median_response_minutes", sa.Double(), nullable=True),
        sa.Column("response_speed", sa.Double(), nullable=True),
        sa.Column("on_time_rate", sa.Double(), nullable=True),
        sa.Column("discrepancy_rate", sa.Double(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "score BETWEEN 0 AND 100", name=op.f("ck_reliability_score_score_range")
        ),
        sa.CheckConstraint(
            "(acceptance_rate IS NULL OR acceptance_rate BETWEEN 0 AND 1)"
            " AND (on_time_rate IS NULL OR on_time_rate BETWEEN 0 AND 1)"
            " AND (discrepancy_rate IS NULL OR discrepancy_rate BETWEEN 0 AND 1)"
            " AND (response_speed IS NULL OR response_speed >= 0)",
            name=op.f("ck_reliability_score_rates"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], name=op.f("fk_reliability_score_org_id_organization")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reliability_score")),
        sa.UniqueConstraint("org_id", name=op.f("uq_reliability_score_org_id")),
    )
    op.create_table(
        "credit_ledger",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("shortage_id", sa.Uuid(), nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint("delta <> 0", name=op.f("ck_credit_ledger_delta_nonzero")),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], name=op.f("fk_credit_ledger_org_id_organization")
        ),
        sa.ForeignKeyConstraint(
            ["shortage_id"], ["shortage.id"], name=op.f("fk_credit_ledger_shortage_id_shortage")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credit_ledger")),
        sa.UniqueConstraint("org_id", "shortage_id", name="uq_credit_ledger_org_shortage"),
    )
    op.create_index(op.f("ix_credit_ledger_org_id"), "credit_ledger", ["org_id"], unique=False)
    op.execute(APPEND_ONLY_FN)
    op.execute(
        "CREATE TRIGGER credit_ledger_no_update_delete BEFORE UPDATE OR DELETE ON credit_ledger "
        "FOR EACH ROW EXECUTE FUNCTION credit_ledger_append_only()"
    )
    op.execute(
        "CREATE TRIGGER credit_ledger_no_truncate BEFORE TRUNCATE ON credit_ledger "
        "FOR EACH STATEMENT EXECUTE FUNCTION credit_ledger_append_only()"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_credit_ledger_org_id"), table_name="credit_ledger")
    op.drop_table("credit_ledger")
    op.execute("DROP FUNCTION credit_ledger_append_only()")
    op.drop_table("reliability_score")
