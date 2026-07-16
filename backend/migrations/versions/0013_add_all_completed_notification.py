"""Make the all-completed broadcast notification optional.

Revision ID: 0013_all_completed_notice
Revises: 0012_reminder_broadcast
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_all_completed_notice"
down_revision: str | None = "0012_reminder_broadcast"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("reminders") as batch:
        batch.add_column(
            sa.Column("notify_on_all_completed", sa.Boolean(), server_default="0", nullable=False)
        )
    with op.batch_alter_table("reminder_occurrences") as batch:
        batch.add_column(
            sa.Column(
                "notify_on_all_completed_snapshot",
                sa.Boolean(),
                server_default="0",
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("reminder_occurrences") as batch:
        batch.drop_column("notify_on_all_completed_snapshot")
    with op.batch_alter_table("reminders") as batch:
        batch.drop_column("notify_on_all_completed")
