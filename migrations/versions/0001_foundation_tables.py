"""Foundation tables

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "heartbeats",
        sa.Column("process", sa.String(length=200), nullable=False),
        sa.Column("beat_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("process"),
    )
    op.create_table(
        "orgs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agents_org_id"), "agents", ["org_id"], unique=False)
    op.create_table(
        "budget_ledger",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("spent_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("calls", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "day"),
    )
    op.create_index(op.f("ix_budget_ledger_org_id"), "budget_ledger", ["org_id"], unique=False)
    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("business_hours", sa.JSON(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "slug"),
    )
    op.create_index(op.f("ix_teams_org_id"), "teams", ["org_id"], unique=False)
    op.create_table(
        "leads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_by_message_id", sa.Uuid(), nullable=False),
        sa.Column("arrival_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("item", sa.String(length=200), nullable=True),
        sa.Column("possible_duplicates", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('pool', 'owned', 'nurture', 'won', 'lost', 'not_real')", name="lead_state"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("created_by_message_id"),
    )
    op.create_index(op.f("ix_leads_org_id"), "leads", ["org_id"], unique=False)
    op.create_index(op.f("ix_leads_team_id"), "leads", ["team_id"], unique=False)
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("auth_method", sa.String(length=16), nullable=False),
        sa.Column("basic_user", sa.String(length=200), nullable=True),
        sa.Column("basic_password_hash", sa.Text(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=True),
        sa.Column("hmac_secret_env", sa.String(length=100), nullable=True),
        sa.Column("field_mapping", sa.JSON(), nullable=False),
        sa.Column("default_team_id", sa.Uuid(), nullable=True),
        sa.Column("delivery_id_header", sa.String(length=100), nullable=True),
        sa.Column("relay_senders", sa.JSON(), nullable=False),
        sa.Column("tracking_numbers", sa.JSON(), nullable=False),
        sa.Column("spam_senders", sa.JSON(), nullable=False),
        sa.Column("honeypot_field", sa.String(length=100), nullable=True),
        sa.CheckConstraint("auth_method IN ('basic', 'token', 'hmac')", name="source_auth"),
        sa.CheckConstraint("kind IN ('email', 'form', 'call')", name="source_kind"),
        sa.ForeignKeyConstraint(
            ["default_team_id"],
            ["teams.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(op.f("ix_sources_org_id"), "sources", ["org_id"], unique=False)
    op.create_table(
        "identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("value", sa.String(length=320), nullable=False),
        sa.CheckConstraint("kind IN ('phone', 'email')", name="identity_kind"),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lead_id", "kind", "value"),
    )
    op.create_index("identities_lookup", "identities", ["org_id", "kind", "value"], unique=False)
    op.create_index(op.f("ix_identities_org_id"), "identities", ["org_id"], unique=False)
    op.create_table(
        "lead_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("cause", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lead_id", "cause"),
        sa.UniqueConstraint("lead_id", "seq"),
    )
    op.create_index(op.f("ix_lead_events_org_id"), "lead_events", ["org_id"], unique=False)
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("source_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("raw", sa.LargeBinary(), nullable=False),
        sa.Column("body_sha256", sa.String(length=64), nullable=False),
        sa.Column("delivery_key", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=16), nullable=True),
        sa.Column("extracted", sa.JSON(), nullable=True),
        sa.Column("lead_id", sa.Uuid(), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("clean_body_sha256", sa.String(length=64), nullable=True),
        sa.Column("needs_reread", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "status IN ('received', 'processing') OR (reason IS NOT NULL AND reason <> '')",
            name="terminal_has_reason",
        ),
        sa.CheckConstraint(
            "status IN ('received', 'processing', 'lead_created', 'attached', 'not_a_lead', 'duplicate_content', 'failed')",
            name="message_status",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "delivery_key"),
    )
    op.create_index(op.f("ix_messages_lead_id"), "messages", ["lead_id"], unique=False)
    op.create_index(op.f("ix_messages_org_id"), "messages", ["org_id"], unique=False)
    op.create_index(
        "messages_source_body",
        "messages",
        ["source_id", "body_sha256", "received_at"],
        unique=False,
    )
    op.create_table(
        "activities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index(op.f("ix_activities_lead_id"), "activities", ["lead_id"], unique=False)
    op.create_index(op.f("ix_activities_org_id"), "activities", ["org_id"], unique=False)
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint("state IN ('queued', 'running', 'done', 'failed')", name="job_state"),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", "kind"),
    )
    op.create_index(op.f("ix_jobs_org_id"), "jobs", ["org_id"], unique=False)
    op.create_index("jobs_ready", "jobs", ["state", "run_after"], unique=False)
    op.create_table(
        "model_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_model_calls_org_id"), "model_calls", ["org_id"], unique=False)
    # leads and messages point at each other, so this key is added once both exist.
    op.create_foreign_key(
        "leads_created_by_message_id_fkey", "leads", "messages", ["created_by_message_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("leads_created_by_message_id_fkey", "leads", type_="foreignkey")
    op.drop_index(op.f("ix_model_calls_org_id"), table_name="model_calls")
    op.drop_table("model_calls")
    op.drop_index("jobs_ready", table_name="jobs")
    op.drop_index(op.f("ix_jobs_org_id"), table_name="jobs")
    op.drop_table("jobs")
    op.drop_index(op.f("ix_activities_org_id"), table_name="activities")
    op.drop_index(op.f("ix_activities_lead_id"), table_name="activities")
    op.drop_table("activities")
    op.drop_index("messages_source_body", table_name="messages")
    op.drop_index(op.f("ix_messages_org_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_lead_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_lead_events_org_id"), table_name="lead_events")
    op.drop_table("lead_events")
    op.drop_index(op.f("ix_identities_org_id"), table_name="identities")
    op.drop_index("identities_lookup", table_name="identities")
    op.drop_table("identities")
    op.drop_index(op.f("ix_sources_org_id"), table_name="sources")
    op.drop_table("sources")
    op.drop_index(op.f("ix_leads_team_id"), table_name="leads")
    op.drop_index(op.f("ix_leads_org_id"), table_name="leads")
    op.drop_table("leads")
    op.drop_index(op.f("ix_teams_org_id"), table_name="teams")
    op.drop_table("teams")
    op.drop_index(op.f("ix_budget_ledger_org_id"), table_name="budget_ledger")
    op.drop_table("budget_ledger")
    op.drop_index(op.f("ix_agents_org_id"), table_name="agents")
    op.drop_table("agents")
    op.drop_table("orgs")
    op.drop_table("heartbeats")
