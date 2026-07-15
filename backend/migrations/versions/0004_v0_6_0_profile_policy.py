"""expand AI Profile runtime policy

Revision ID: 0004_v0_6_0
Revises: 0003_v0_6_0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_v0_6_0"
down_revision: str | None = "0003_v0_6_0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_profiles", sa.Column("description", sa.Text(), server_default="", nullable=False)
    )
    op.add_column(
        "ai_profiles",
        sa.Column("capability", sa.String(length=20), server_default="classify", nullable=False),
    )
    op.add_column(
        "ai_profiles",
        sa.Column("output_language", sa.String(length=20), server_default="auto", nullable=False),
    )
    op.add_column(
        "ai_profiles",
        sa.Column(
            "reasoning_effort",
            sa.String(length=30),
            server_default="provider_default",
            nullable=False,
        ),
    )
    op.add_column(
        "ai_profiles",
        sa.Column("verbosity", sa.String(length=20), server_default="standard", nullable=False),
    )
    op.add_column(
        "ai_profiles",
        sa.Column("include_reason", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "ai_profiles",
        sa.Column("max_reason_characters", sa.Integer(), server_default="200", nullable=False),
    )
    op.add_column(
        "ai_profiles",
        sa.Column("system_instructions", sa.Text(), server_default="", nullable=False),
    )
    op.add_column("ai_profiles", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_profiles", "deleted_at")
    op.drop_column("ai_profiles", "system_instructions")
    op.drop_column("ai_profiles", "max_reason_characters")
    op.drop_column("ai_profiles", "include_reason")
    op.drop_column("ai_profiles", "verbosity")
    op.drop_column("ai_profiles", "reasoning_effort")
    op.drop_column("ai_profiles", "output_language")
    op.drop_column("ai_profiles", "capability")
    op.drop_column("ai_profiles", "description")
