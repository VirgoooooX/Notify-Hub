"""Add application profiles and isolate persisted runtime state by profile."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0018_application_profiles"
down_revision: str | None = "0017_add_api_client_mp_browser_permission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_PROFILE_ID = "profile_notify_hub"
DEFAULT_PROFILE_KEY = "notify-hub"
PROFILE_DEFAULTS = {
    "outbound_enabled": True,
    "callback_enabled": True,
    "menu_enabled": True,
    "conversation_enabled": True,
    "interactive_enabled": True,
    "mobile_enabled": True,
    "broadcast_enabled": True,
}


def _profile_default() -> sa.TextClause:
    return sa.text(f"'{DEFAULT_PROFILE_ID}'")


def _add_profile_column(table_name: str, column_name: str = "profile_id") -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns(table_name)}
    if column_name in columns:
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.add_column(
            sa.Column(
                column_name,
                sa.String(64),
                nullable=False,
                server_default=_profile_default(),
            )
        )


def _drop_profile_column(table_name: str, column_name: str = "profile_id") -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns(table_name)}
    if column_name not in columns:
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.drop_column(column_name)


def _seed_default_profile() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)
    if bind.execute(
        sa.select(sa.literal(1))
        .select_from(sa.table("application_profiles"))
        .where(sa.column("id") == DEFAULT_PROFILE_ID)
    ).first():
        return
    op.bulk_insert(
        sa.table(
            "application_profiles",
            sa.column("id", sa.String(64)),
            sa.column("key", sa.String(100)),
            sa.column("name", sa.String(200)),
            sa.column("enabled", sa.Boolean()),
            sa.column("is_default", sa.Boolean()),
            sa.column("capabilities", sa.JSON()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        ),
        [
            {
                "id": DEFAULT_PROFILE_ID,
                "key": DEFAULT_PROFILE_KEY,
                "name": "Notify Hub",
                "enabled": True,
                "is_default": True,
                "capabilities": PROFILE_DEFAULTS,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def _backfill_members_and_state() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)
    people = bind.execute(sa.text("SELECT id FROM people")).fetchall()
    if people:
        member_table = sa.table(
            "profile_members",
            sa.column("id", sa.String(64)),
            sa.column("profile_id", sa.String(64)),
            sa.column("person_id", sa.String(64)),
            sa.column("enabled", sa.Boolean()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        op.bulk_insert(
            member_table,
            [
                {
                    "id": f"pm_{uuid4().hex}",
                    "profile_id": DEFAULT_PROFILE_ID,
                    "person_id": str(row[0]),
                    "enabled": True,
                    "created_at": now,
                    "updated_at": now,
                }
                for row in people
            ],
        )

    identities = bind.execute(
        sa.text(
            "SELECT id, latest_interactive_occurrence_id "
            "FROM wecom_identities "
            "WHERE latest_interactive_occurrence_id IS NOT NULL"
        )
    ).fetchall()
    if identities:
        state_table = sa.table(
            "profile_user_states",
            sa.column("id", sa.String(64)),
            sa.column("profile_id", sa.String(64)),
            sa.column("wecom_identity_id", sa.String(64)),
            sa.column("latest_interactive_occurrence_id", sa.String(64)),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        op.bulk_insert(
            state_table,
            [
                {
                    "id": f"pus_{uuid4().hex}",
                    "profile_id": DEFAULT_PROFILE_ID,
                    "wecom_identity_id": str(row[0]),
                    "latest_interactive_occurrence_id": str(row[1]),
                    "created_at": now,
                    "updated_at": now,
                }
                for row in identities
            ],
        )


def upgrade() -> None:
    op.create_table(
        "application_profiles",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_profiles")),
        sa.UniqueConstraint("key", name="uq_application_profiles_key"),
    )
    op.create_index(
        "ix_application_profiles_enabled", "application_profiles", ["enabled"], unique=False
    )
    op.create_table(
        "wecom_profile_configs",
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("callback_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["application_profiles.id"],
            name=op.f("fk_wecom_profile_configs_profile_id_application_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("profile_id", name=op.f("pk_wecom_profile_configs")),
    )
    op.create_table(
        "profile_members",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("person_id", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            name=op.f("fk_profile_members_person_id_people"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["application_profiles.id"],
            name=op.f("fk_profile_members_profile_id_application_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_members")),
        sa.UniqueConstraint("profile_id", "person_id", name="uq_profile_members_profile_person"),
    )
    op.create_index(
        "ix_profile_members_enabled", "profile_members", ["profile_id", "enabled"], unique=False
    )
    op.create_table(
        "profile_user_states",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("wecom_identity_id", sa.String(64), nullable=False),
        sa.Column("latest_interactive_occurrence_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["latest_interactive_occurrence_id"],
            ["reminder_occurrences.id"],
            name=op.f(
                "fk_profile_user_states_latest_interactive_occurrence_id_reminder_occurrences"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["application_profiles.id"],
            name=op.f("fk_profile_user_states_profile_id_application_profiles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["wecom_identity_id"],
            ["wecom_identities.id"],
            name=op.f("fk_profile_user_states_wecom_identity_id_wecom_identities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_user_states")),
        sa.UniqueConstraint(
            "profile_id", "wecom_identity_id", name="uq_profile_user_states_profile_identity"
        ),
    )
    op.create_index(
        "ix_profile_user_states_latest",
        "profile_user_states",
        ["profile_id", "latest_interactive_occurrence_id"],
        unique=False,
    )
    op.create_table(
        "media_provider_refs",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("provider_media_id", sa.String(256), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["media_assets.id"],
            name=op.f("fk_media_provider_refs_asset_id_media_assets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["application_profiles.id"],
            name=op.f("fk_media_provider_refs_profile_id_application_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_provider_refs")),
        sa.UniqueConstraint(
            "asset_id",
            "profile_id",
            "channel",
            name="uq_media_provider_refs_asset_profile_channel",
        ),
    )
    op.create_index(
        "ix_media_provider_refs_expiry", "media_provider_refs", ["expires_at"], unique=False
    )

    for table_name in (
        "api_clients",
        "events",
        "notifications",
        "deliveries",
        "reminders",
        "reminder_occurrences",
        "reminder_drafts",
        "incoming_messages",
        "interaction_events",
        "conversation_sessions",
        "notification_actions",
        "plugins",
        "mp_articles",
    ):
        _add_profile_column(table_name)
    _add_profile_column("reminder_occurrences", "profile_id_snapshot")

    with op.batch_alter_table("events") as batch_op:
        batch_op.drop_constraint("uq_event_source_key", type_="unique")
        batch_op.create_unique_constraint(
            "uq_event_source_key", ["profile_id", "source_type", "source_id", "event_key"]
        )
    with op.batch_alter_table("deliveries") as batch_op:
        batch_op.drop_constraint("uq_delivery_target", type_="unique")
        batch_op.create_unique_constraint(
            "uq_delivery_target",
            ["profile_id", "notification_id", "channel", "recipient_type", "recipient_id"],
        )
    with op.batch_alter_table("incoming_messages") as batch_op:
        batch_op.drop_constraint("uq_incoming_message_dedupe", type_="unique")
        batch_op.create_unique_constraint(
            "uq_incoming_message_dedupe", ["profile_id", "channel", "dedupe_key"]
        )
    with op.batch_alter_table("interaction_events") as batch_op:
        batch_op.drop_constraint("uq_interaction_event_dedupe", type_="unique")
        batch_op.create_unique_constraint(
            "uq_interaction_event_dedupe", ["profile_id", "channel", "dedupe_key"]
        )
    with op.batch_alter_table("conversation_sessions") as batch_op:
        batch_op.drop_constraint("uq_conversation_sessions_wecom_identity_id", type_="unique")
        batch_op.create_unique_constraint(
            "uq_conversation_sessions_profile_identity",
            ["profile_id", "wecom_identity_id"],
        )

    _seed_default_profile()
    _backfill_members_and_state()


def downgrade() -> None:
    for table_name, constraint_name, columns in (
        (
            "conversation_sessions",
            "uq_conversation_sessions_profile_identity",
            ["wecom_identity_id"],
        ),
        ("interaction_events", "uq_interaction_event_dedupe", ["channel", "dedupe_key"]),
        ("incoming_messages", "uq_incoming_message_dedupe", ["channel", "dedupe_key"]),
        (
            "deliveries",
            "uq_delivery_target",
            ["notification_id", "channel", "recipient_type", "recipient_id"],
        ),
        ("events", "uq_event_source_key", ["source_type", "source_id", "event_key"]),
    ):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_constraint(constraint_name, type_="unique")
            batch_op.create_unique_constraint(constraint_name, columns)
    for table_name, column_name in (
        ("mp_articles", "profile_id"),
        ("plugins", "profile_id"),
        ("notification_actions", "profile_id"),
        ("conversation_sessions", "profile_id"),
        ("interaction_events", "profile_id"),
        ("incoming_messages", "profile_id"),
        ("reminder_drafts", "profile_id"),
        ("reminder_occurrences", "profile_id_snapshot"),
        ("reminder_occurrences", "profile_id"),
        ("reminders", "profile_id"),
        ("deliveries", "profile_id"),
        ("notifications", "profile_id"),
        ("events", "profile_id"),
        ("api_clients", "profile_id"),
    ):
        _drop_profile_column(table_name, column_name)
    op.drop_index("ix_media_provider_refs_expiry", table_name="media_provider_refs")
    op.drop_table("media_provider_refs")
    op.drop_index("ix_profile_user_states_latest", table_name="profile_user_states")
    op.drop_table("profile_user_states")
    op.drop_index("ix_profile_members_enabled", table_name="profile_members")
    op.drop_table("profile_members")
    op.drop_table("wecom_profile_configs")
    op.drop_index("ix_application_profiles_enabled", table_name="application_profiles")
    op.drop_table("application_profiles")
