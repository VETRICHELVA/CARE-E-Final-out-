"""S04: catalog (products, authorizations, supplier offers) and inventory batches

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05 19:57:39.350052
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("requires_cold_chain", sa.Boolean(), nullable=False),
        sa.Column("temp_min_c", sa.Double(), nullable=True),
        sa.Column("temp_max_c", sa.Double(), nullable=True),
        sa.Column("default_min_shelf_life_days", sa.Integer(), nullable=False),
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
        sa.CheckConstraint("default_min_shelf_life_days >= 0", name=op.f("ck_product_shelf_life")),
        sa.CheckConstraint(
            "temp_min_c IS NULL OR temp_max_c IS NULL OR temp_min_c <= temp_max_c",
            name=op.f("ck_product_temp_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product")),
        sa.UniqueConstraint("code", name=op.f("uq_product_code")),
    )
    op.create_table(
        "product_authorization",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
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
            ["org_id"],
            ["organization.id"],
            name=op.f("fk_product_authorization_org_id_organization"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["product.id"], name=op.f("fk_product_authorization_product_id_product")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_authorization")),
        sa.UniqueConstraint("org_id", "product_id", name=op.f("uq_product_authorization_org_id")),
    )
    op.create_index(
        op.f("ix_product_authorization_product_id"),
        "product_authorization",
        ["product_id"],
        unique=False,
    )
    op.create_table(
        "supplier_offer",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("unit_price_paise", sa.Integer(), nullable=False),
        sa.Column("lead_time_hours", sa.Integer(), nullable=False),
        sa.Column("available_qty", sa.Integer(), nullable=False),
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
            "unit_price_paise >= 0 AND lead_time_hours >= 0 AND available_qty >= 0",
            name=op.f("ck_supplier_offer_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], name=op.f("fk_supplier_offer_org_id_organization")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["product.id"], name=op.f("fk_supplier_offer_product_id_product")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_offer")),
        sa.UniqueConstraint("org_id", "product_id", name=op.f("uq_supplier_offer_org_id")),
    )
    op.create_index(
        op.f("ix_supplier_offer_product_id"), "supplier_offer", ["product_id"], unique=False
    )
    op.create_table(
        "inventory_batch",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("facility_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("batch_no", sa.String(length=64), nullable=False),
        sa.Column("on_hand", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False),
        sa.Column("allocated", sa.Integer(), nullable=False),
        sa.Column("safety_stock", sa.Integer(), nullable=False),
        sa.Column("quarantined", sa.Integer(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("unit_cost_paise", sa.Integer(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
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
            "on_hand >= 0 AND reserved >= 0 AND allocated >= 0 AND safety_stock >= 0"
            " AND quarantined >= 0 AND unit_cost_paise >= 0",
            name=op.f("ck_inventory_batch_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["facility_id"], ["facility.id"], name=op.f("fk_inventory_batch_facility_id_facility")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], name=op.f("fk_inventory_batch_org_id_organization")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["product.id"], name=op.f("fk_inventory_batch_product_id_product")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_batch")),
        sa.UniqueConstraint(
            "facility_id", "product_id", "batch_no", name=op.f("uq_inventory_batch_facility_id")
        ),
    )
    op.create_index(op.f("ix_inventory_batch_org_id"), "inventory_batch", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_inventory_batch_product_id"), "inventory_batch", ["product_id"], unique=False
    )
    op.create_table(
        "verification_event",
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("counted_qty", sa.Integer(), nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
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
            "method IN ('MANUAL','SCAN')", name=op.f("ck_verification_event_method")
        ),
        sa.CheckConstraint("counted_qty >= 0", name=op.f("ck_verification_event_counted_qty")),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["inventory_batch.id"],
            name=op.f("fk_verification_event_batch_id_inventory_batch"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name=op.f("fk_verification_event_user_id_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_event")),
    )
    op.create_index(
        op.f("ix_verification_event_batch_id"), "verification_event", ["batch_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_verification_event_batch_id"), table_name="verification_event")
    op.drop_table("verification_event")
    op.drop_index(op.f("ix_inventory_batch_product_id"), table_name="inventory_batch")
    op.drop_index(op.f("ix_inventory_batch_org_id"), table_name="inventory_batch")
    op.drop_table("inventory_batch")
    op.drop_index(op.f("ix_supplier_offer_product_id"), table_name="supplier_offer")
    op.drop_table("supplier_offer")
    op.drop_index(op.f("ix_product_authorization_product_id"), table_name="product_authorization")
    op.drop_table("product_authorization")
    op.drop_table("product")
