"""Add the WeChat Official Account article library.

Revision ID: 0014_mp_articles
Revises: 0013_all_completed_notice
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_mp_articles"
down_revision: str | None = "0013_all_completed_notice"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mp_articles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("author", sa.String(length=100), nullable=False),
        sa.Column("digest", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_html", sa.Text(), nullable=False),
        sa.Column("cover_url", sa.String(length=2048), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("event_key", sa.String(length=200), nullable=True),
        sa.Column("source_type", sa.String(length=30), nullable=True),
        sa.Column("source_id", sa.String(length=100), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=True),
        sa.Column("notification_id", sa.String(length=64), nullable=True),
        sa.Column("delivery_id", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("ai_profile", sa.String(length=100), nullable=True),
        sa.Column("ai_status", sa.String(length=20), nullable=True),
        sa.Column("provider_draft_media_id", sa.String(length=200), nullable=True),
        sa.Column("provider_publish_id", sa.String(length=200), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["notification_id"], ["notifications.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_id", name="uq_mp_articles_delivery_id"),
    )
    op.create_index(
        "ix_mp_articles_status_created", "mp_articles", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_mp_articles_status_created", table_name="mp_articles")
    op.drop_table("mp_articles")