"""track whether plugin schedules follow their manifest default

Revision ID: 0006_v0_7_0
Revises: 0005_v0_6_0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_v0_7_0"
down_revision: str | None = "0005_v0_6_0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plugins",
        sa.Column("schedule_inherits_default", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("plugins", "schedule_inherits_default")
