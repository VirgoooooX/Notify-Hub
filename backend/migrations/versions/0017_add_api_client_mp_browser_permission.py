"""Add allow_mp_browser permission to api_clients."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_add_api_client_mp_browser_permission"
down_revision: str | None = "0016_x_source_health"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = [c["name"] for c in insp.get_columns("api_clients")]
    if "allow_mp_browser" not in columns:
        with op.batch_alter_table("api_clients") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "allow_mp_browser",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("api_clients") as batch_op:
        batch_op.drop_column("allow_mp_browser")
