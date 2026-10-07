"""S14: devices and sensor readings

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-07 15:43:17.579030
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("battery_level", sa.Integer(), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("type IN ('COLD_BOX')", name=op.f("ck_device_type")),
        sa.CheckConstraint("battery_level BETWEEN 0 AND 100", name=op.f("ck_device_battery_level")),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], name=op.f("fk_device_org_id_organization")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device")),
        sa.UniqueConstraint("device_id", name=op.f("uq_device_device_id")),
    )
    op.create_index(op.f("ix_device_org_id"), "device", ["org_id"], unique=False)
    op.create_table(
        "sensor_reading",
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("temp_c", sa.Double(), nullable=False),
        sa.Column("battery", sa.Integer(), nullable=True),
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
        sa.CheckConstraint("battery BETWEEN 0 AND 100", name=op.f("ck_sensor_reading_battery")),
        sa.ForeignKeyConstraint(
            ["device_id"], ["device.device_id"], name=op.f("fk_sensor_reading_device_id_device")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sensor_reading")),
        sa.UniqueConstraint("device_id", "ts", name=op.f("uq_sensor_reading_device_id")),
    )


def downgrade() -> None:
    op.drop_table("sensor_reading")
    op.drop_index(op.f("ix_device_org_id"), table_name="device")
    op.drop_table("device")
