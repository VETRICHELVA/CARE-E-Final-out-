"""S05: shortages, match runs and candidates

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-07 15:34:58.025215
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shortage",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("qty_required", sa.Integer(), nullable=False),
        sa.Column("qty_local_usable", sa.Integer(), nullable=False),
        sa.Column("shortfall", sa.Integer(), nullable=False),
        sa.Column("required_by", sa.DateTime(timezone=True), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column("min_shelf_life_days", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("parent_shortage_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
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
        sa.CheckConstraint("priority IN ('CRITICAL','ROUTINE')", name=op.f("ck_shortage_priority")),
        sa.CheckConstraint("source IN ('FORM','CHAT')", name=op.f("ck_shortage_source")),
        sa.CheckConstraint(
            "status IN ('DRAFT','OPEN','MATCHING','AWAITING_DECISION','IN_FULFILLMENT',"
            "'RECEIVED','RESOLVED','PARTIALLY_RESOLVED','CANCELLED')",
            name=op.f("ck_shortage_status"),
        ),
        sa.CheckConstraint(
            "qty_required >= 0 AND qty_local_usable >= 0 AND min_shelf_life_days >= 0",
            name=op.f("ck_shortage_non_negative"),
        ),
        sa.CheckConstraint(
            "shortfall = GREATEST(0, qty_required - qty_local_usable)",
            name=op.f("ck_shortage_shortfall"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["app_user.id"], name=op.f("fk_shortage_created_by_app_user")
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"], ["facility.id"], name=op.f("fk_shortage_facility_id_facility")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], name=op.f("fk_shortage_org_id_organization")
        ),
        sa.ForeignKeyConstraint(
            ["parent_shortage_id"],
            ["shortage.id"],
            name=op.f("fk_shortage_parent_shortage_id_shortage"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["product.id"], name=op.f("fk_shortage_product_id_product")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shortage")),
    )
    op.create_index(op.f("ix_shortage_org_id"), "shortage", ["org_id"], unique=False)
    op.create_table(
        "match_run",
        sa.Column("shortage_id", sa.Uuid(), nullable=False),
        sa.Column("run_no", sa.Integer(), nullable=False),
        sa.Column("triggered_by", sa.String(length=24), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("excluded_org_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("planned_resolution", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            "triggered_by IN ('CREATE','DECLINE','EXPIRY','MANUAL','RECOMMENDATION_EXPIRED')",
            name=op.f("ck_match_run_triggered_by"),
        ),
        sa.ForeignKeyConstraint(
            ["shortage_id"], ["shortage.id"], name=op.f("fk_match_run_shortage_id_shortage")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_match_run")),
        sa.UniqueConstraint("shortage_id", "run_no", name=op.f("uq_match_run_shortage_id")),
    )
    op.create_table(
        "candidate",
        sa.Column("match_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_org_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=8), nullable=False),
        sa.Column("batch_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("transferable_qty", sa.Integer(), nullable=True),
        sa.Column("offered_qty", sa.Integer(), nullable=True),
        sa.Column("gate_results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("landed_cost_paise", sa.BigInteger(), nullable=True),
        sa.Column("eta_hours", sa.Double(), nullable=False),
        sa.Column("reliability", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
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
            "source_type IN ('HOSPITAL','SUPPLIER')", name=op.f("ck_candidate_source_type")
        ),
        sa.ForeignKeyConstraint(
            ["match_run_id"], ["match_run.id"], name=op.f("fk_candidate_match_run_id_match_run")
        ),
        sa.ForeignKeyConstraint(
            ["source_org_id"],
            ["organization.id"],
            name=op.f("fk_candidate_source_org_id_organization"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_candidate")),
    )
    op.create_index(op.f("ix_candidate_match_run_id"), "candidate", ["match_run_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_candidate_match_run_id"), table_name="candidate")
    op.drop_table("candidate")
    op.drop_table("match_run")
    op.drop_index(op.f("ix_shortage_org_id"), table_name="shortage")
    op.drop_table("shortage")
