"""Track each WeCom user's latest successfully delivered interactive occurrence.

Revision ID: 0011_latest_interactive_occurrence
Revises: 0010_reminder_center
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_latest_interactive_occurrence"
down_revision: str | None = "0010_reminder_center"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("wecom_identities") as batch:
        batch.add_column(sa.Column("latest_interactive_occurrence_id", sa.String(64)))
        batch.create_foreign_key(
            "fk_wecom_identities_latest_interactive_occurrence",
            "reminder_occurrences",
            ["latest_interactive_occurrence_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("wecom_identities") as batch:
        batch.drop_constraint(
            "fk_wecom_identities_latest_interactive_occurrence", type_="foreignkey"
        )
        batch.drop_column("latest_interactive_occurrence_id")
