"""Complete reminder schedule, draft, access, and interaction persistence.

Revision ID: 0010_reminder_center
Revises: 0009_escalation_duration
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_reminder_center"
down_revision: str | None = "0009_escalation_duration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reminders",
        sa.Column("schedule_config", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column("reminders", sa.Column("start_at", sa.DateTime(timezone=True)))
    op.add_column("reminders", sa.Column("end_at", sa.DateTime(timezone=True)))
    op.add_column(
        "reminders",
        sa.Column("misfire_policy", sa.String(20), nullable=False, server_default="fire_once"),
    )

    for name in ("allow_reminders", "allow_recurring", "allow_cron", "allow_interactive"):
        op.add_column(
            "api_clients",
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    op.add_column(
        "api_clients",
        sa.Column("max_active_reminders", sa.Integer(), nullable=False, server_default="10"),
    )

    op.create_table(
        "reminder_drafts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("parsed_data", sa.JSON(), nullable=False),
        sa.Column("parse_method", sa.String(20), nullable=False),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("confirmed_reminder_id", sa.String(64)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_reminder_drafts_owner_status", "reminder_drafts", ["created_by", "status"]
    )
    op.create_index(
        "ix_reminder_drafts_expiry", "reminder_drafts", ["status", "expires_at"]
    )
    with op.batch_alter_table("conversation_sessions") as batch:
        batch.add_column(sa.Column("draft_id", sa.String(64)))
        batch.create_foreign_key(
            "fk_conversation_sessions_draft_id",
            "reminder_drafts",
            ["draft_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.add_column(
        "interaction_events",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "interaction_events",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
    )
    op.add_column("interaction_events", sa.Column("last_error", sa.String(200)))


def downgrade() -> None:
    op.drop_column("interaction_events", "last_error")
    op.drop_column("interaction_events", "max_attempts")
    op.drop_column("interaction_events", "attempt_count")
    with op.batch_alter_table("conversation_sessions") as batch:
        batch.drop_constraint("fk_conversation_sessions_draft_id", type_="foreignkey")
        batch.drop_column("draft_id")
    op.drop_index("ix_reminder_drafts_expiry", table_name="reminder_drafts")
    op.drop_index("ix_reminder_drafts_owner_status", table_name="reminder_drafts")
    op.drop_table("reminder_drafts")
    op.drop_column("api_clients", "max_active_reminders")
    for name in ("allow_interactive", "allow_cron", "allow_recurring", "allow_reminders"):
        op.drop_column("api_clients", name)
    op.drop_column("reminders", "misfire_policy")
    op.drop_column("reminders", "end_at")
    op.drop_column("reminders", "start_at")
    op.drop_column("reminders", "schedule_config")
