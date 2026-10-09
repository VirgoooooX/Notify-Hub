"""Track the center's Profile revision without removing local history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_ai_hub_profile_catalog"
down_revision: str | None = "0019_ai_model_reasoning_levels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_profiles", sa.Column("hub_revision", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_profiles", "hub_revision")
