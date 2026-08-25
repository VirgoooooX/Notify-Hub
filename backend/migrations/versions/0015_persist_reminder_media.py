"""Make existing reminder-uploaded media persistent.

Reminder media can be reused by future occurrences, so the generic temporary
media retention policy must not expire assets that are referenced by a reminder
definition or occurrence snapshot.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_persist_reminder_media"
down_revision: str | None = "0014_mp_articles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE media_assets
            SET expires_at = NULL
            WHERE (
                EXISTS (
                    SELECT 1
                    FROM reminders
                    WHERE reminders.media_asset_id = media_assets.id
                )
                OR EXISTS (
                    SELECT 1
                    FROM reminder_occurrences
                    WHERE reminder_occurrences.media_asset_id_snapshot = media_assets.id
                )
              )
            """
        )
    )


def downgrade() -> None:
    # The previous expiry timestamps are not recoverable from the database.
    # Keep the data migration irreversible rather than inventing new expiry
    # dates that could break active recurring reminders again.
    pass
