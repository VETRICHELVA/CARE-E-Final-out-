"""S12: receipts and reconciliations

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-08 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "receipt",
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("shortage_id", sa.Uuid(), nullable=False),
        sa.Column("expected", sa.Integer(), nullable=False),
        sa.Column("received", sa.Integer(), nullable=False),
        sa.Column("accepted", sa.Integer(), nullable=False),
        sa.Column("rejected", sa.Integer(), nullable=False),
        sa.Column("condition", sa.String(length=20), nullable=False),
        sa.Column("inspection_note", sa.String(), nullable=True),
        sa.Column("received_by", sa.Uuid(), nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "condition IN ('GOOD','DAMAGED','TEMPERATURE_ISSUE')",
            name=op.f("ck_receipt_condition"),
        ),
        sa.CheckConstraint(
            "expected > 0 AND received >= 0 AND accepted >= 0 AND rejected >= 0",
            name=op.f("ck_receipt_non_negative"),
        ),
        sa.CheckConstraint("received <= expected", name=op.f("ck_receipt_received_le_expected")),
        sa.CheckConstraint(
            "accepted + rejected = received", name=op.f("ck_receipt_accepted_rejected")
        ),
        sa.CheckConstraint("(accepted = 0) = (batch_id IS NULL)", name=op.f("ck_receipt_batch")),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["inventory_batch.id"], name=op.f("fk_receipt_batch_id_inventory_batch")
        ),
        sa.ForeignKeyConstraint(
            ["received_by"], ["app_user.id"], name=op.f("fk_receipt_received_by_app_user")
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"], ["shipment.id"], name=op.f("fk_receipt_shipment_id_shipment")
        ),
        sa.ForeignKeyConstraint(
            ["shortage_id"], ["shortage.id"], name=op.f("fk_receipt_shortage_id_shortage")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_receipt")),
        sa.UniqueConstraint("shipment_id", name=op.f("uq_receipt_shipment_id")),
    )
    op.create_index(op.f("ix_receipt_shortage_id"), "receipt", ["shortage_id"], unique=False)
    op.create_table(
        "reconciliation",
        sa.Column("shortage_id", sa.Uuid(), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("expected", sa.Integer(), nullable=False),
        sa.Column("accepted", sa.Integer(), nullable=False),
        sa.Column("discrepancy", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=10), nullable=False),
        sa.Column("residual_shortage_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "outcome IN ('CONFIRMED','PARTIAL')", name=op.f("ck_reconciliation_outcome")
        ),
        sa.CheckConstraint(
            "discrepancy = expected - accepted", name=op.f("ck_reconciliation_discrepancy")
        ),
        sa.CheckConstraint(
            "(outcome = 'PARTIAL') = (residual_shortage_id IS NOT NULL)",
            name=op.f("ck_reconciliation_residual"),
        ),
        sa.ForeignKeyConstraint(
            ["residual_shortage_id"],
            ["shortage.id"],
            name=op.f("fk_reconciliation_residual_shortage_id_shortage"),
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"], ["shipment.id"], name=op.f("fk_reconciliation_shipment_id_shipment")
        ),
        sa.ForeignKeyConstraint(
            ["shortage_id"], ["shortage.id"], name=op.f("fk_reconciliation_shortage_id_shortage")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reconciliation")),
        sa.UniqueConstraint("shipment_id", name=op.f("uq_reconciliation_shipment_id")),
    )
    op.create_index(
        op.f("ix_reconciliation_shortage_id"), "reconciliation", ["shortage_id"], unique=False
    )
    # GET /notifications lists a user's notifications newest first.
    op.create_index(
        "ix_notification_user_created", "notification", ["user_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_notification_user_created", table_name="notification")
    op.drop_index(op.f("ix_reconciliation_shortage_id"), table_name="reconciliation")
    op.drop_table("reconciliation")
    op.drop_index(op.f("ix_receipt_shortage_id"), table_name="receipt")
    op.drop_table("receipt")
