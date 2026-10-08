"""S09: recommendations, purchase orders, minimal shipments, notifications

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-07 17:17:36.578092
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_notification_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification")),
    )
    op.create_index(op.f("ix_notification_user_id"), "notification", ["user_id"], unique=False)
    op.create_table(
        "purchase_order",
        sa.Column("shortage_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_org_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("unit_price_paise", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("eta", sa.DateTime(timezone=True), nullable=False),
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
            "status IN ('SENT','ACKNOWLEDGED','DISPATCHED','DELIVERED','REJECTED')",
            name=op.f("ck_purchase_order_status"),
        ),
        sa.CheckConstraint(
            "qty > 0 AND unit_price_paise >= 0", name=op.f("ck_purchase_order_amounts")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["product.id"], name=op.f("fk_purchase_order_product_id_product")
        ),
        sa.ForeignKeyConstraint(
            ["shortage_id"], ["shortage.id"], name=op.f("fk_purchase_order_shortage_id_shortage")
        ),
        sa.ForeignKeyConstraint(
            ["supplier_org_id"],
            ["organization.id"],
            name=op.f("fk_purchase_order_supplier_org_id_organization"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_order")),
    )
    op.create_index(
        op.f("ix_purchase_order_shortage_id"), "purchase_order", ["shortage_id"], unique=False
    )
    op.create_index(
        op.f("ix_purchase_order_supplier_org_id"),
        "purchase_order",
        ["supplier_org_id"],
        unique=False,
    )
    op.create_table(
        "recommendation",
        sa.Column("shortage_id", sa.Uuid(), nullable=False),
        sa.Column("match_run_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("lines", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("alternatives", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("explanation", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
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
            name=op.f("ck_recommendation_reason_source"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED','ESCALATED','EXPIRED')",
            name=op.f("ck_recommendation_status"),
        ),
        sa.CheckConstraint(
            "type IN ('TRANSFER','TRANSFER_SPLIT','BUY')", name=op.f("ck_recommendation_type")
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["app_user.id"], name=op.f("fk_recommendation_decided_by_app_user")
        ),
        sa.ForeignKeyConstraint(
            ["match_run_id"],
            ["match_run.id"],
            name=op.f("fk_recommendation_match_run_id_match_run"),
        ),
        sa.ForeignKeyConstraint(
            ["shortage_id"], ["shortage.id"], name=op.f("fk_recommendation_shortage_id_shortage")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendation")),
        sa.UniqueConstraint("match_run_id", name=op.f("uq_recommendation_match_run_id")),
    )
    op.create_index(
        "ix_recommendation_open_expiry",
        "recommendation",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('PENDING','ESCALATED')"),
    )
    op.create_index(
        op.f("ix_recommendation_shortage_id"), "recommendation", ["shortage_id"], unique=False
    )
    op.create_index(
        "uq_recommendation_open",
        "recommendation",
        ["shortage_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING','ESCALATED')"),
    )
    op.create_table(
        "shipment",
        sa.Column("shortage_id", sa.Uuid(), nullable=False),
        sa.Column("source_request_id", sa.Uuid(), nullable=True),
        sa.Column("purchase_order_id", sa.Uuid(), nullable=True),
        sa.Column("from_org_id", sa.Uuid(), nullable=False),
        sa.Column("to_org_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("requires_cold_chain", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("driver_id", sa.Uuid(), nullable=True),
        sa.Column("vehicle_id", sa.Uuid(), nullable=True),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("planned_eta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("route_geometry", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            "status IN ('CREATED','ASSIGNED','PICKED_UP','IN_TRANSIT','DELIVERED','RECONCILED')",
            name=op.f("ck_shipment_status"),
        ),
        sa.CheckConstraint(
            "(source_request_id IS NULL) <> (purchase_order_id IS NULL)",
            name=op.f("ck_shipment_one_origin"),
        ),
        sa.CheckConstraint("qty > 0", name=op.f("ck_shipment_qty_positive")),
        sa.ForeignKeyConstraint(
            ["device_id"], ["device.id"], name=op.f("fk_shipment_device_id_device")
        ),
        sa.ForeignKeyConstraint(
            ["from_org_id"], ["organization.id"], name=op.f("fk_shipment_from_org_id_organization")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["product.id"], name=op.f("fk_shipment_product_id_product")
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"],
            ["purchase_order.id"],
            name=op.f("fk_shipment_purchase_order_id_purchase_order"),
        ),
        sa.ForeignKeyConstraint(
            ["shortage_id"], ["shortage.id"], name=op.f("fk_shipment_shortage_id_shortage")
        ),
        sa.ForeignKeyConstraint(
            ["source_request_id"],
            ["source_request.id"],
            name=op.f("fk_shipment_source_request_id_source_request"),
        ),
        sa.ForeignKeyConstraint(
            ["to_org_id"], ["organization.id"], name=op.f("fk_shipment_to_org_id_organization")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipment")),
        sa.UniqueConstraint("purchase_order_id", name=op.f("uq_shipment_purchase_order_id")),
        sa.UniqueConstraint("source_request_id", name=op.f("uq_shipment_source_request_id")),
    )
    op.create_index(op.f("ix_shipment_from_org_id"), "shipment", ["from_org_id"], unique=False)
    op.create_index(op.f("ix_shipment_shortage_id"), "shipment", ["shortage_id"], unique=False)
    op.create_index(op.f("ix_shipment_to_org_id"), "shipment", ["to_org_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_shipment_to_org_id"), table_name="shipment")
    op.drop_index(op.f("ix_shipment_shortage_id"), table_name="shipment")
    op.drop_index(op.f("ix_shipment_from_org_id"), table_name="shipment")
    op.drop_table("shipment")
    op.drop_index(
        "uq_recommendation_open",
        table_name="recommendation",
        postgresql_where=sa.text("status IN ('PENDING','ESCALATED')"),
    )
    op.drop_index(op.f("ix_recommendation_shortage_id"), table_name="recommendation")
    op.drop_index(
        "ix_recommendation_open_expiry",
        table_name="recommendation",
        postgresql_where=sa.text("status IN ('PENDING','ESCALATED')"),
    )
    op.drop_table("recommendation")
    op.drop_index(op.f("ix_purchase_order_supplier_org_id"), table_name="purchase_order")
    op.drop_index(op.f("ix_purchase_order_shortage_id"), table_name="purchase_order")
    op.drop_table("purchase_order")
    op.drop_index(op.f("ix_notification_user_id"), table_name="notification")
    op.drop_table("notification")
