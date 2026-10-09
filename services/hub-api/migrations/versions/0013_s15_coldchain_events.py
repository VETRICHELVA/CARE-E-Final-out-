"""S15: cold-chain events

Revision ID: 0013_s15
Revises: 0009
Create Date: 2026-10-08 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_s15"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coldchain_event",
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
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
            "type IN ('EXCURSION','DEVICE_SILENT','RECOVERED')",
            name=op.f("ck_coldchain_event_type"),
        ),
        sa.CheckConstraint(
            "severity IN ('ALERT','WARNING','INFO')",
            name=op.f("ck_coldchain_event_severity"),
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["device.device_id"],
            name=op.f("fk_coldchain_event_device_id_device"),
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["shipment.id"],
            name=op.f("fk_coldchain_event_shipment_id_shipment"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coldchain_event")),
    )
    op.create_index(
        "ix_coldchain_event_shipment_ts", "coldchain_event", ["shipment_id", "ts"], unique=False
    )
    # The silent-device check and the rules read a shipment's newest readings by time.
    op.create_index(
        "ix_sensor_reading_shipment_ts", "sensor_reading", ["shipment_id", "ts"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_sensor_reading_shipment_ts", table_name="sensor_reading")
    op.drop_index("ix_coldchain_event_shipment_ts", table_name="coldchain_event")
    op.drop_table("coldchain_event")
