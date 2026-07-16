"""add reminder escalation duration

Revision ID: 0009_escalation_duration
Revises: 0008_occurrence_claim
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_escalation_duration"
down_revision: str | None = "0008_occurrence_claim"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("reminders") as batch_op:
        batch_op.add_column(sa.Column("escalation_stop_after_seconds", sa.Integer()))
    op.execute(
        """
        UPDATE reminders
        SET escalation_stop_after_seconds = CAST(
            (julianday(stop_at) - julianday(scheduled_at)) * 86400 AS INTEGER
        )
        WHERE stop_at IS NOT NULL AND scheduled_at IS NOT NULL
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("reminders") as batch_op:
        batch_op.drop_column("escalation_stop_after_seconds")
