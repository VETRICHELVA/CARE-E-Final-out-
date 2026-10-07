"""S06: source requests and holds

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07 15:45:17.641046
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_request",
        sa.Column("shortage_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("source_org_id", sa.Uuid(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sla_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_by", sa.Uuid(), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decline_reason", sa.String(), nullable=True),
        sa.Column("reason_source", sa.String(length=8), nullable=True),
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
            "reason_source IS NULL OR reason_source IN ('USER','SYSTEM')",
            name=op.f("ck_source_request_reason_source"),
        ),
        sa.CheckConstraint(
            "status IN ('REQUESTED','TENTATIVE_HOLD','DECLINED','EXPIRED','SUPERSEDED',"
            "'CONFIRMED')",
            name=op.f("ck_source_request_status"),
        ),
        sa.CheckConstraint("qty > 0", name=op.f("ck_source_request_qty_positive")),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidate.id"],
            name=op.f("fk_source_request_candidate_id_candidate"),
        ),
        sa.ForeignKeyConstraint(
            ["responded_by"], ["app_user.id"], name=op.f("fk_source_request_responded_by_app_user")
        ),
        sa.ForeignKeyConstraint(
            ["shortage_id"], ["shortage.id"], name=op.f("fk_source_request_shortage_id_shortage")
        ),
        sa.ForeignKeyConstraint(
            ["source_org_id"],
            ["organization.id"],
            name=op.f("fk_source_request_source_org_id_organization"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_request")),
    )
    op.create_index(
        "ix_source_request_open_deadline",
        "source_request",
        ["sla_deadline"],
        unique=False,
        postgresql_where=sa.text("status = 'REQUESTED'"),
    )
    op.create_index(
        op.f("ix_source_request_shortage_id"), "source_request", ["shortage_id"], unique=False
    )
    op.create_index(
        op.f("ix_source_request_source_org_id"), "source_request", ["source_org_id"], unique=False
    )
    op.create_index(
        "uq_source_request_open",
        "source_request",
        ["shortage_id", "source_org_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('REQUESTED','TENTATIVE_HOLD')"),
    )
    op.create_table(
        "hold",
        sa.Column("source_request_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            "status IN ('TENTATIVE','FIRM','RELEASED')", name=op.f("ck_hold_status")
        ),
        sa.CheckConstraint("qty > 0", name=op.f("ck_hold_qty_positive")),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["inventory_batch.id"], name=op.f("fk_hold_batch_id_inventory_batch")
        ),
        sa.ForeignKeyConstraint(
            ["source_request_id"],
            ["source_request.id"],
            name=op.f("fk_hold_source_request_id_source_request"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hold")),
    )
    op.create_index(
        "ix_hold_active_batch",
        "hold",
        ["batch_id"],
        unique=False,
        postgresql_where=sa.text("status IN ('TENTATIVE','FIRM')"),
    )
    op.create_index(op.f("ix_hold_source_request_id"), "hold", ["source_request_id"], unique=False)
    op.create_index(
        "ix_hold_tentative_expiry",
        "hold",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'TENTATIVE'"),
    )


def downgrade() -> None:
    op.drop_table("hold")  # its indexes go with it
    op.drop_table("source_request")
