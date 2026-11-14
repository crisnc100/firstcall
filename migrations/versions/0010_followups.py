"""Follow-ups, the nightly runs that open them, and each team's cadence

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "teams", sa.Column("cadence", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
    )
    op.create_table(
        "followups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("orgs.id"), nullable=False, index=True),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id"), nullable=False, index=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("step", sa.String(32), nullable=False),
        sa.Column("next_step", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('open', 'done', 'cancelled')", name="followup_state"),
    )
    op.create_index(
        "followups_one_open",
        "followups",
        ["lead_id"],
        unique=True,
        postgresql_where=sa.text("state = 'open'"),
    )
    op.create_table(
        "followup_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("orgs.id"), nullable=False, index=True),
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("leads", sa.Integer(), nullable=False),
        sa.Column("opened", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.UniqueConstraint("team_id", "local_date"),
    )


def downgrade() -> None:
    op.drop_table("followup_runs")
    op.drop_index("followups_one_open", table_name="followups")
    op.drop_table("followups")
    op.drop_column("teams", "cadence")
