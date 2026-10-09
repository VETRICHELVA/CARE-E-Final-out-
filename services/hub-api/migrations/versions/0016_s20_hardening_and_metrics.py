"""S20: supplier unit price on match candidates (network metrics), outbox pruning index and mark

Revision ID: 0016_s20
Revises: 0015_s19
Create Date: 2026-10-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_s20"
down_revision: str | Sequence[str] | None = "0015_s19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The offer's unit price as each match run saw it (supplier candidates only). Runs made
    # before S20 have none; the metrics count their transfers as unpriced, never guess.
    op.add_column("candidate", sa.Column("unit_price_paise", sa.BigInteger(), nullable=True))
    # The pruning job scans published events by age (S20).
    op.create_index(
        "ix_event_outbox_published_at",
        "event_outbox",
        ["published_at"],
        postgresql_where=sa.text("published_at IS NOT NULL"),
    )
    # The highest pruned seq: replay from an older Last-Event-ID answers `reset`.
    op.create_table(
        "event_prune_mark",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("seq", sa.BigInteger(), server_default="0", nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_event_prune_mark_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_prune_mark")),
    )
    op.execute("INSERT INTO event_prune_mark (id, seq) VALUES (1, 0)")


def downgrade() -> None:
    op.drop_table("event_prune_mark")
    op.drop_index("ix_event_outbox_published_at", table_name="event_outbox")
    op.drop_column("candidate", "unit_price_paise")
