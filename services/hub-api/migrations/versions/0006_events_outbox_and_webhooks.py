"""S07: events outbox, webhooks, STOCK_CHANGE match run trigger

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-07 16:15:36.584273
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TRIGGERS = "'CREATE','DECLINE','EXPIRY','MANUAL','RECOMMENDATION_EXPIRED'"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_match_run_triggered_by"), "match_run", type_="check")
    op.create_check_constraint(
        op.f("ck_match_run_triggered_by"),
        "match_run",
        f"triggered_by IN ({TRIGGERS},'STOCK_CHANGE')",
    )
    op.execute("CREATE SEQUENCE event_outbox_publish_seq")
    op.create_table(
        "event_outbox",
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("org_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=True),
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
        sa.CheckConstraint("cardinality(org_ids) > 0", name=op.f("ck_event_outbox_has_orgs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_outbox")),
        sa.UniqueConstraint("seq", name=op.f("uq_event_outbox_seq")),
    )
    op.create_index(
        "ix_event_outbox_unpublished",
        "event_outbox",
        ["created_at", "id"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_table(
        "webhook_subscription",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("secret", sa.String(length=128), nullable=False),
        sa.Column("event_types", postgresql.ARRAY(sa.String(length=64)), nullable=False),
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
            name=op.f("fk_webhook_subscription_org_id_organization"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_subscription")),
    )
    op.create_index(
        op.f("ix_webhook_subscription_org_id"), "webhook_subscription", ["org_id"], unique=False
    )
    op.create_table(
        "webhook_delivery",
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=True),
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
            "status IN ('PENDING','DELIVERED','RETRY_SCHEDULED','FAILED')",
            name=op.f("ck_webhook_delivery_status"),
        ),
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_webhook_delivery_attempt_positive")),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["event_outbox.id"],
            name=op.f("fk_webhook_delivery_event_id_event_outbox"),
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["webhook_subscription.id"],
            name=op.f("fk_webhook_delivery_subscription_id_webhook_subscription"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_delivery")),
        sa.UniqueConstraint(
            "subscription_id",
            "event_id",
            "attempt",
            name=op.f("uq_webhook_delivery_subscription_id"),
        ),
    )
    op.create_index(
        "ix_webhook_delivery_due",
        "webhook_delivery",
        ["next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        op.f("ix_webhook_delivery_event_id"), "webhook_delivery", ["event_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_webhook_delivery_event_id"), table_name="webhook_delivery")
    op.drop_index(
        "ix_webhook_delivery_due",
        table_name="webhook_delivery",
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.drop_table("webhook_delivery")
    op.drop_index(op.f("ix_webhook_subscription_org_id"), table_name="webhook_subscription")
    op.drop_table("webhook_subscription")
    op.drop_index(
        "ix_event_outbox_unpublished",
        table_name="event_outbox",
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.drop_table("event_outbox")
    op.execute("DROP SEQUENCE event_outbox_publish_seq")
    # Fails while STOCK_CHANGE runs exist; they cannot be described with the old triggers.
    op.drop_constraint(op.f("ck_match_run_triggered_by"), "match_run", type_="check")
    op.create_check_constraint(
        op.f("ck_match_run_triggered_by"), "match_run", f"triggered_by IN ({TRIGGERS})"
    )
