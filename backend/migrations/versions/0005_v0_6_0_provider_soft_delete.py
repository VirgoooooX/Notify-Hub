"""add AI Provider soft deletion

Revision ID: 0005_v0_6_0
Revises: 0004_v0_6_0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_v0_6_0"
down_revision: str | None = "0004_v0_6_0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_providers", sa.Column("deleted_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("ai_providers", "deleted_at")
