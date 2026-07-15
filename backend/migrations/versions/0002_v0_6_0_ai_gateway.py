"""add AI Gateway control-plane tables

Revision ID: 0002_v0_6_0
Revises: 0001_v0_3_0
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_v0_6_0"
down_revision: str | None = "0001_v0_3_0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_providers",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("preset", sa.String(length=40), nullable=False),
        sa.Column("protocol", sa.String(length=40), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("allow_private_network", sa.Boolean(), nullable=False),
        sa.Column("timeout_seconds", sa.Float(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("verify_tls", sa.Boolean(), nullable=False),
        sa.Column("structured_output_mode", sa.String(length=20), nullable=False),
        sa.Column("custom_headers_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("custom_query", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_providers")),
    )
    op.create_table(
        "ai_profiles",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=300), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("response_format", sa.String(length=20), nullable=False),
        sa.Column("timeout_seconds", sa.Float(), nullable=False),
        sa.Column("cache_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("daily_request_limit", sa.Integer(), nullable=True),
        sa.Column("daily_token_limit", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["ai_providers.id"],
            name=op.f("fk_ai_profiles_provider_id_ai_providers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_profiles")),
    )
    op.create_index(
        "ix_ai_profiles_provider", "ai_profiles", ["provider_id", "enabled"], unique=False
    )
    op.create_table(
        "ai_response_cache",
        sa.Column("cache_key", sa.String(length=500), nullable=True),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("profile_revision", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["ai_profiles.id"],
            name=op.f("fk_ai_response_cache_profile_id_ai_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_response_cache")),
        sa.UniqueConstraint(
            "profile_id",
            "profile_revision",
            "prompt_version",
            "input_hash",
            name="uq_ai_cache_input",
        ),
    )
    op.create_index(
        "ix_ai_response_cache_expires",
        "ai_response_cache",
        ["expires_at"],
        unique=False,
    )
    op.create_table(
        "ai_invocations",
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("plugin_id", sa.String(length=64), nullable=True),
        sa.Column("plugin_run_id", sa.String(length=64), nullable=True),
        sa.Column("use_case", sa.String(length=100), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["ai_profiles.id"],
            name=op.f("fk_ai_invocations_profile_id_ai_profiles"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_invocations")),
    )
    op.create_index(
        "ix_ai_invocations_profile_created",
        "ai_invocations",
        ["profile_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_invocations_plugin_created",
        "ai_invocations",
        ["plugin_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_invocations_plugin_created", table_name="ai_invocations")
    op.drop_index("ix_ai_invocations_profile_created", table_name="ai_invocations")
    op.drop_table("ai_invocations")
    op.drop_index("ix_ai_response_cache_expires", table_name="ai_response_cache")
    op.drop_table("ai_response_cache")
    op.drop_index("ix_ai_profiles_provider", table_name="ai_profiles")
    op.drop_table("ai_profiles")
    op.drop_table("ai_providers")
