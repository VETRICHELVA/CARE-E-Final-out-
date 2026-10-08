"""S11: vehicles, drivers, shipment routing, legs, location pings, CONSUMED holds;
S14: device and reading links to shipments

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-07 17:51:26.092805
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
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
        "vehicle",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("reg_no", sa.String(length=32), nullable=False),
        sa.Column("has_cold_chain", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], name=op.f("fk_vehicle_org_id_organization")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicle")),
        sa.UniqueConstraint("org_id", "reg_no", name=op.f("uq_vehicle_org_id")),
    )
    op.create_index(op.f("ix_vehicle_org_id"), "vehicle", ["org_id"], unique=False)
    op.create_table(
        "driver",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], name=op.f("fk_driver_org_id_organization")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name=op.f("fk_driver_user_id_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_driver")),
        sa.UniqueConstraint("user_id", name=op.f("uq_driver_user_id")),
    )
    op.create_index(op.f("ix_driver_org_id"), "driver", ["org_id"], unique=False)
    op.create_table(
        "location_ping",
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lng", sa.Double(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "lat BETWEEN -90 AND 90 AND lng BETWEEN -180 AND 180",
            name=op.f("ck_location_ping_range"),
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"], ["shipment.id"], name=op.f("fk_location_ping_shipment_id_shipment")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_location_ping")),
    )
    op.create_index(
        "ix_location_ping_shipment_ts", "location_ping", ["shipment_id", "ts"], unique=False
    )
    op.create_table(
        "shipment_leg",
        sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("stop_type", sa.String(length=8), nullable=False),
        sa.Column("place", sa.String(), nullable=False),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lng", sa.Double(), nullable=False),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "stop_type IN ('PICKUP','DROP')", name=op.f("ck_shipment_leg_stop_type")
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["shipment.id"],
            name=op.f("fk_shipment_leg_shipment_id_shipment"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipment_leg")),
        sa.UniqueConstraint("shipment_id", "seq", name=op.f("uq_shipment_leg_shipment_id")),
    )

    # S14: a device rides with one shipment; its readings are linked while the shipment moves.
    op.add_column("device", sa.Column("assigned_shipment_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_device_assigned_shipment_id"), "device", ["assigned_shipment_id"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_device_assigned_shipment_id_shipment"),
        "device",
        "shipment",
        ["assigned_shipment_id"],
        ["id"],
    )
    op.add_column("sensor_reading", sa.Column("shipment_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_sensor_reading_shipment_id"), "sensor_reading", ["shipment_id"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_sensor_reading_shipment_id_shipment"),
        "sensor_reading",
        "shipment",
        ["shipment_id"],
        ["id"],
    )

    # S11: assignment, route and status history on the shipment.
    op.add_column("shipment", sa.Column("carrier_org_id", sa.Uuid(), nullable=True))
    op.add_column("shipment", sa.Column("eta", sa.DateTime(timezone=True), nullable=True))
    op.add_column("shipment", sa.Column("route_distance_km", sa.Double(), nullable=True))
    op.add_column("shipment", sa.Column("route_provider", sa.String(length=10), nullable=True))
    op.add_column(
        "shipment",
        sa.Column(
            "status_history",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index(
        op.f("ix_shipment_carrier_org_id"), "shipment", ["carrier_org_id"], unique=False
    )
    op.create_index(op.f("ix_shipment_driver_id"), "shipment", ["driver_id"], unique=False)
    op.create_index(op.f("ix_shipment_status"), "shipment", ["status"], unique=False)
    op.create_foreign_key(
        op.f("fk_shipment_carrier_org_id_organization"),
        "shipment",
        "organization",
        ["carrier_org_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_shipment_vehicle_id_vehicle"), "shipment", "vehicle", ["vehicle_id"], ["id"]
    )
    op.create_foreign_key(
        op.f("fk_shipment_driver_id_driver"), "shipment", "driver", ["driver_id"], ["id"]
    )
    op.create_check_constraint(
        op.f("ck_shipment_route_provider"),
        "shipment",
        "route_provider IS NULL OR route_provider IN ('OSRM','HAVERSINE')",
    )
    op.create_check_constraint(
        op.f("ck_shipment_assignment"),
        "shipment",
        "(status = 'CREATED') = (driver_id IS NULL)"
        " AND (driver_id IS NULL) = (vehicle_id IS NULL)"
        " AND (driver_id IS NULL) = (carrier_org_id IS NULL)",
    )
    # Existing (S09) shipments start their history at creation.
    op.execute(
        "UPDATE shipment SET status_history = jsonb_build_array(jsonb_build_object("
        "'from', NULL, 'to', 'CREATED', 'at', to_jsonb(created_at)))"
    )

    # S11: a FIRM hold is CONSUMED at pickup (business-rules.md §9).
    op.drop_constraint(op.f("ck_hold_status"), "hold", type_="check")
    op.create_check_constraint(
        op.f("ck_hold_status"), "hold", "status IN ('TENTATIVE','FIRM','RELEASED','CONSUMED')"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_hold_status"), "hold", type_="check")
    op.create_check_constraint(
        op.f("ck_hold_status"), "hold", "status IN ('TENTATIVE','FIRM','RELEASED')"
    )
    op.drop_constraint(op.f("ck_shipment_assignment"), "shipment", type_="check")
    op.drop_constraint(op.f("ck_shipment_route_provider"), "shipment", type_="check")
    op.drop_constraint(op.f("fk_shipment_driver_id_driver"), "shipment", type_="foreignkey")
    op.drop_constraint(op.f("fk_shipment_vehicle_id_vehicle"), "shipment", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_shipment_carrier_org_id_organization"), "shipment", type_="foreignkey"
    )
    op.drop_index(op.f("ix_shipment_status"), table_name="shipment")
    op.drop_index(op.f("ix_shipment_driver_id"), table_name="shipment")
    op.drop_index(op.f("ix_shipment_carrier_org_id"), table_name="shipment")
    op.drop_column("shipment", "status_history")
    op.drop_column("shipment", "route_provider")
    op.drop_column("shipment", "route_distance_km")
    op.drop_column("shipment", "eta")
    op.drop_column("shipment", "carrier_org_id")
    op.drop_constraint(
        op.f("fk_sensor_reading_shipment_id_shipment"), "sensor_reading", type_="foreignkey"
    )
    op.drop_index(op.f("ix_sensor_reading_shipment_id"), table_name="sensor_reading")
    op.drop_column("sensor_reading", "shipment_id")
    op.drop_constraint(
        op.f("fk_device_assigned_shipment_id_shipment"), "device", type_="foreignkey"
    )
    op.drop_index(op.f("ix_device_assigned_shipment_id"), table_name="device")
    op.drop_column("device", "assigned_shipment_id")
    op.drop_table("shipment_leg")
    op.drop_index("ix_location_ping_shipment_ts", table_name="location_ping")
    op.drop_table("location_ping")
    op.drop_index(op.f("ix_driver_org_id"), table_name="driver")
    op.drop_table("driver")
    op.drop_index(op.f("ix_vehicle_org_id"), table_name="vehicle")
    op.drop_table("vehicle")
