"""Store provider model reasoning levels discovered during synchronization."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_ai_model_reasoning_levels"
down_revision: str | None = "0018_application_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_provider_models") as batch_op:
        batch_op.add_column(sa.Column("supported_reasoning_levels", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("default_reasoning_level", sa.String(30), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ai_provider_models") as batch_op:
        batch_op.drop_column("default_reasoning_level")
        batch_op.drop_column("supported_reasoning_levels")
