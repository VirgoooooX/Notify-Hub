"""Persist time rules inside an interactive reminder occurrence."""

import sqlalchemy as sa
from alembic import op

revision = "0021_reminder_repeat_rules"
down_revision = "0020_ai_hub_profile_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reminders", sa.Column("repeat_config", sa.JSON(), server_default="{}", nullable=False)
    )
    op.add_column(
        "reminder_occurrences",
        sa.Column("repeat_config_snapshot", sa.JSON(), server_default="{}", nullable=False),
    )
    op.add_column(
        "reminder_occurrences",
        sa.Column("require_ack_snapshot", sa.Boolean(), server_default="0", nullable=False),
    )
    op.execute(
        "UPDATE reminder_occurrences SET require_ack_snapshot = 1 "
        "WHERE repeat_interval_seconds_snapshot IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("reminder_occurrences", "require_ack_snapshot")
    op.drop_column("reminder_occurrences", "repeat_config_snapshot")
    op.drop_column("reminders", "repeat_config")
