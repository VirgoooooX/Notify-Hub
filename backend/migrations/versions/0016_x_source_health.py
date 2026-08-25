"""Persist X source and account health incidents."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_x_source_health"
down_revision: str | None = "0015_persist_reminder_media"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "x_source_health",
        sa.Column("scope_key", sa.String(length=180), nullable=False),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("consecutive_successes", sa.Integer(), nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=True),
        sa.Column("incident_started_at", sa.DateTime(), nullable=True),
        sa.Column("first_failure_at", sa.DateTime(), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_post_at", sa.DateTime(), nullable=True),
        sa.Column("last_alert_at", sa.DateTime(), nullable=True),
        sa.Column("stale_since", sa.DateTime(), nullable=True),
        sa.Column("stale_last_alert_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("scope_key"),
    )
    op.create_index(
        "ix_x_source_health_provider_status",
        "x_source_health",
        ["provider", "status"],
    )
    op.create_index(
        "ix_x_source_health_username",
        "x_source_health",
        ["username"],
    )


def downgrade() -> None:
    op.drop_index("ix_x_source_health_username", table_name="x_source_health")
    op.drop_index("ix_x_source_health_provider_status", table_name="x_source_health")
    op.drop_table("x_source_health")
