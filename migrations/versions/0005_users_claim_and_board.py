"""Users, claim and board: users, sessions, alerts, push, escalations and lead ownership

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "email"),
    )
    op.create_index(op.f("ix_users_org_id"), "users", ["org_id"], unique=False)
    op.create_table(
        "inbox_emails",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_inbox_emails_org_id"), "inbox_emails", ["org_id"], unique=False)
    op.create_index(op.f("ix_inbox_emails_user_id"), "inbox_emails", ["user_id"], unique=False)
    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.CheckConstraint("role IN ('salesperson', 'team_lead')", name="membership_role"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "team_id"),
    )
    op.create_index(op.f("ix_memberships_org_id"), "memberships", ["org_id"], unique=False)
    op.create_index(op.f("ix_memberships_team_id"), "memberships", ["team_id"], unique=False)
    op.create_index(op.f("ix_memberships_user_id"), "memberships", ["user_id"], unique=False)
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=200), nullable=False),
        sa.Column("auth", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint"),
    )
    op.create_index(
        op.f("ix_push_subscriptions_org_id"), "push_subscriptions", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_push_subscriptions_user_id"), "push_subscriptions", ["user_id"], unique=False
    )
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_user_sessions_org_id"), "user_sessions", ["org_id"], unique=False)
    op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"], unique=False)
    op.create_table(
        "alerts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("cause", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("email_state", sa.String(length=16), nullable=False),
        sa.Column("email_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "email_state IN ('pending', 'sent', 'refused')", name="alert_email_state"
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
            ["team_id"],
            ["teams.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lead_id", "kind", "user_id", "cause"),
    )
    op.create_index("alerts_email_pending", "alerts", ["email_state", "user_id"], unique=False)
    op.create_index(op.f("ix_alerts_lead_id"), "alerts", ["lead_id"], unique=False)
    op.create_index(op.f("ix_alerts_org_id"), "alerts", ["org_id"], unique=False)
    op.create_index(op.f("ix_alerts_user_id"), "alerts", ["user_id"], unique=False)
    op.create_table(
        "escalations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lead_id", "level"),
    )
    op.create_index(op.f("ix_escalations_lead_id"), "escalations", ["lead_id"], unique=False)
    op.create_index(op.f("ix_escalations_org_id"), "escalations", ["org_id"], unique=False)
    op.create_table(
        "push_sends",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_status", sa.Integer(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('pending', 'sent', 'failed', 'gone')", name="push_state"),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["push_subscriptions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alert_id", "subscription_id"),
    )
    op.create_index(op.f("ix_push_sends_alert_id"), "push_sends", ["alert_id"], unique=False)
    op.create_index(op.f("ix_push_sends_org_id"), "push_sends", ["org_id"], unique=False)
    op.create_index("push_sends_pending", "push_sends", ["state"], unique=False)
    op.add_column("leads", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.add_column("leads", sa.Column("pool_since", sa.DateTime(timezone=True), nullable=True))
    op.add_column("leads", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "leads", sa.Column("claim_minutes", sa.Numeric(precision=12, scale=2), nullable=True)
    )
    op.add_column(
        "leads",
        sa.Column("claim_business_minutes", sa.Numeric(precision=12, scale=2), nullable=True),
    )
    op.create_index(op.f("ix_leads_owner_id"), "leads", ["owner_id"], unique=False)
    op.create_foreign_key("leads_owner_id_fkey", "leads", "users", ["owner_id"], ["id"])
    op.execute("UPDATE leads SET pool_since = arrival_at WHERE state = 'pool'")
    op.add_column(
        "teams", sa.Column("escalation", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
    )
    op.alter_column("teams", "escalation", server_default=None)


def downgrade() -> None:
    op.drop_column("teams", "escalation")
    op.drop_constraint("leads_owner_id_fkey", "leads", type_="foreignkey")
    op.drop_index(op.f("ix_leads_owner_id"), table_name="leads")
    op.drop_column("leads", "claim_business_minutes")
    op.drop_column("leads", "claim_minutes")
    op.drop_column("leads", "claimed_at")
    op.drop_column("leads", "pool_since")
    op.drop_column("leads", "owner_id")
    op.drop_index("push_sends_pending", table_name="push_sends")
    op.drop_index(op.f("ix_push_sends_org_id"), table_name="push_sends")
    op.drop_index(op.f("ix_push_sends_alert_id"), table_name="push_sends")
    op.drop_table("push_sends")
    op.drop_index(op.f("ix_escalations_org_id"), table_name="escalations")
    op.drop_index(op.f("ix_escalations_lead_id"), table_name="escalations")
    op.drop_table("escalations")
    op.drop_index(op.f("ix_alerts_user_id"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_org_id"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_lead_id"), table_name="alerts")
    op.drop_index("alerts_email_pending", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_org_id"), table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index(op.f("ix_push_subscriptions_user_id"), table_name="push_subscriptions")
    op.drop_index(op.f("ix_push_subscriptions_org_id"), table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
    op.drop_index(op.f("ix_memberships_user_id"), table_name="memberships")
    op.drop_index(op.f("ix_memberships_team_id"), table_name="memberships")
    op.drop_index(op.f("ix_memberships_org_id"), table_name="memberships")
    op.drop_table("memberships")
    op.drop_index(op.f("ix_inbox_emails_user_id"), table_name="inbox_emails")
    op.drop_index(op.f("ix_inbox_emails_org_id"), table_name="inbox_emails")
    op.drop_table("inbox_emails")
    op.drop_index(op.f("ix_users_org_id"), table_name="users")
    op.drop_table("users")
