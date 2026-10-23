"""Persist explicit user-requested publisher login maintenance."""

import sqlalchemy as sa
from alembic import op

revision = "0022_reminder_login_actions"
down_revision = "0021_reminder_repeat_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reminders",
        sa.Column("completion_action", sa.String(40), server_default="none", nullable=False),
    )
    op.add_column(
        "reminder_occurrences",
        sa.Column(
            "completion_action_snapshot", sa.String(40), server_default="none", nullable=False
        ),
    )
    op.create_table(
        "reminder_login_actions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "occurrence_recipient_id",
            sa.String(64),
            sa.ForeignKey("reminder_occurrence_recipients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "media_asset_id", sa.String(64), sa.ForeignKey("media_assets.id", ondelete="SET NULL")
        ),
        sa.UniqueConstraint("occurrence_recipient_id", name="uq_login_action_recipient"),
        sa.CheckConstraint("generation > 0", name="ck_login_action_generation"),
        sa.CheckConstraint(
            "status IN ('pending','starting','qr_ready','waiting_scan','failed_pending','failed','completed','cancelled')",
            name="ck_login_action_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("reminder_login_actions")
    op.drop_column("reminder_occurrences", "completion_action_snapshot")
    op.drop_column("reminders", "completion_action")
