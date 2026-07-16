"""Add Web-only reminder broadcast snapshots and reliable announcements.

Revision ID: 0012_reminder_broadcast
Revises: 0011_latest_interactive_occurrence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_reminder_broadcast"
down_revision: str | None = "0011_latest_interactive_occurrence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("reminders") as batch:
        batch.add_column(sa.Column("broadcast", sa.Boolean(), server_default="0", nullable=False))
    with op.batch_alter_table("reminder_occurrences") as batch:
        batch.add_column(
            sa.Column("broadcast_snapshot", sa.Boolean(), server_default="0", nullable=False)
        )
        batch.add_column(sa.Column("broadcast_sent_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("broadcast_completion_announced_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("broadcast_claimed_by", sa.String(100)))
        batch.add_column(sa.Column("broadcast_claim_expires_at", sa.DateTime(timezone=True)))
        batch.create_index(
            "ix_occurrence_broadcast_work",
            ["broadcast_snapshot", "status", "broadcast_sent_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("reminder_occurrences") as batch:
        batch.drop_index("ix_occurrence_broadcast_work")
        batch.drop_column("broadcast_claim_expires_at")
        batch.drop_column("broadcast_claimed_by")
        batch.drop_column("broadcast_completion_announced_at")
        batch.drop_column("broadcast_sent_at")
        batch.drop_column("broadcast_snapshot")
    with op.batch_alter_table("reminders") as batch:
        batch.drop_column("broadcast")
