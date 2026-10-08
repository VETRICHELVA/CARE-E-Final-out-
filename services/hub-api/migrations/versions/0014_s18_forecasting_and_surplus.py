"""S18: forecasting and surplus

Revision ID: 0014_s18
Revises: 0009
Create Date: 2026-10-08 15:55:11.117051
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_s18"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "consumption_record",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint("qty >= 0", name=op.f("ck_consumption_record_qty")),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], name=op.f("fk_consumption_record_org_id_organization")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["product.id"], name=op.f("fk_consumption_record_product_id_product")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consumption_record")),
        sa.UniqueConstraint(
            "org_id", "product_id", "date", name=op.f("uq_consumption_record_org_id")
        ),
    )
    op.create_index(
        op.f("ix_consumption_record_product_id"), "consumption_record", ["product_id"], unique=False
    )
    op.create_table(
        "forecast",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("predicted_qty", sa.Float(), nullable=False),
        sa.Column("lower", sa.Float(), nullable=False),
        sa.Column("upper", sa.Float(), nullable=False),
        sa.Column("model_version", sa.String(length=32), nullable=False),
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
            "0 <= lower AND lower <= predicted_qty AND predicted_qty <= upper",
            name=op.f("ck_forecast_interval"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], name=op.f("fk_forecast_org_id_organization")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["product.id"], name=op.f("fk_forecast_product_id_product")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_forecast")),
        sa.UniqueConstraint("org_id", "product_id", "date", name=op.f("uq_forecast_org_id")),
    )
    op.create_index("ix_forecast_product", "forecast", ["product_id"], unique=False)
    op.create_table(
        "surplus_post",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("min_price_paise", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
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
            "status IN ('OPEN','MATCHED','WITHDRAWN','EXPIRED')",
            name=op.f("ck_surplus_post_status"),
        ),
        sa.CheckConstraint("min_price_paise >= 0", name=op.f("ck_surplus_post_min_price")),
        sa.CheckConstraint("qty > 0", name=op.f("ck_surplus_post_qty")),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["inventory_batch.id"],
            name=op.f("fk_surplus_post_batch_id_inventory_batch"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["app_user.id"], name=op.f("fk_surplus_post_created_by_app_user")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], name=op.f("fk_surplus_post_org_id_organization")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["product.id"], name=op.f("fk_surplus_post_product_id_product")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_surplus_post")),
    )
    op.create_index(op.f("ix_surplus_post_org_id"), "surplus_post", ["org_id"], unique=False)
    op.create_index(
        "ix_surplus_post_product_status", "surplus_post", ["product_id", "status"], unique=False
    )
    op.create_index(
        "uq_surplus_post_live_batch",
        "surplus_post",
        ["batch_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('OPEN','MATCHED')"),
    )
    op.create_table(
        "surplus_match",
        sa.Column("surplus_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("shortage_id", sa.Uuid(), nullable=True),
        sa.Column("stockout_date", sa.Date(), nullable=True),
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
            "(kind = 'SHORTAGE') = (shortage_id IS NOT NULL) "
            "AND (kind = 'FORECAST') = (stockout_date IS NOT NULL)",
            name=op.f("ck_surplus_match_reason"),
        ),
        sa.CheckConstraint("kind IN ('SHORTAGE','FORECAST')", name=op.f("ck_surplus_match_kind")),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], name=op.f("fk_surplus_match_org_id_organization")
        ),
        sa.ForeignKeyConstraint(
            ["shortage_id"], ["shortage.id"], name=op.f("fk_surplus_match_shortage_id_shortage")
        ),
        sa.ForeignKeyConstraint(
            ["surplus_id"],
            ["surplus_post.id"],
            name=op.f("fk_surplus_match_surplus_id_surplus_post"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_surplus_match")),
        sa.UniqueConstraint("surplus_id", "org_id", name=op.f("uq_surplus_match_surplus_id")),
    )
    op.create_index("ix_surplus_match_org", "surplus_match", ["org_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_surplus_match_org", table_name="surplus_match")
    op.drop_table("surplus_match")
    op.drop_index(
        "uq_surplus_post_live_batch",
        table_name="surplus_post",
        postgresql_where=sa.text("status IN ('OPEN','MATCHED')"),
    )
    op.drop_index("ix_surplus_post_product_status", table_name="surplus_post")
    op.drop_index(op.f("ix_surplus_post_org_id"), table_name="surplus_post")
    op.drop_table("surplus_post")
    op.drop_index("ix_forecast_product", table_name="forecast")
    op.drop_table("forecast")
    op.drop_index(op.f("ix_consumption_record_product_id"), table_name="consumption_record")
    op.drop_table("consumption_record")
