"""add provider model allowlist

Revision ID: 0003_v0_6_0
Revises: 0002_v0_6_0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_v0_6_0"
down_revision: str | None = "0002_v0_6_0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_models",
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=300), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["ai_providers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "model_id", name="uq_ai_provider_model"),
    )
    op.create_index(
        "ix_ai_provider_models_allowed",
        "ai_provider_models",
        ["provider_id", "available", "enabled"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_provider_models_allowed", table_name="ai_provider_models")
    op.drop_table("ai_provider_models")
