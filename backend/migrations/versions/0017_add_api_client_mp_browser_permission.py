"""Add allow_mp_browser permission to api_clients."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_add_api_client_mp_browser_permission"
down_revision: str | None = "0016_x_source_health"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_clients",
        sa.Column(
            "allow_mp_browser",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("api_clients", "allow_mp_browser", server_default=None)


def downgrade() -> None:
    op.drop_column("api_clients", "allow_mp_browser")
