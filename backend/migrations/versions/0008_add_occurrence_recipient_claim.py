"""add occurrence recipient claim fields

Revision ID: 0008_occurrence_claim
Revises: 0007_add_reminder_occurrence
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_occurrence_claim"
down_revision: str | None = "0007_add_reminder_occurrence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("reminder_occurrence_recipients") as batch_op:
        batch_op.add_column(sa.Column("acknowledged_by", sa.String(length=64)))
        batch_op.add_column(sa.Column("claimed_by", sa.String(length=100)))
        batch_op.add_column(sa.Column("claim_expires_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    with op.batch_alter_table("reminder_occurrence_recipients") as batch_op:
        batch_op.drop_column("claim_expires_at")
        batch_op.drop_column("claimed_by")
        batch_op.drop_column("acknowledged_by")
