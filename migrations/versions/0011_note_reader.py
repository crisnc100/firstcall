"""The note reader: note read state, suggestions, note jobs, and where model fields came from.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notes",
        sa.Column("read_state", sa.String(16), nullable=False, server_default="pending"),
    )
    op.add_column("notes", sa.Column("read_reason", sa.String(32), nullable=True))
    op.add_column("notes", sa.Column("clean_note", sa.Text(), nullable=True))
    op.add_column("notes", sa.Column("model_answer", sa.JSON(), nullable=True))
    op.add_column("notes", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "note_read_state", "notes", "read_state IN ('pending', 'read', 'needs_model', 'failed')"
    )
    # Notes written before the reader have no read job; the reread pass picks these up.
    op.execute("UPDATE notes SET read_state = 'needs_model', read_reason = 'written_before_reader'")
    op.add_column(
        "lead_profiles",
        sa.Column("model_from", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )

    op.create_table(
        "note_suggestions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("orgs.id"), nullable=False, index=True),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id"), nullable=False, index=True),
        sa.Column("note_id", sa.Uuid(), sa.ForeignKey("notes.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("closed_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("note_id", "kind"),
        sa.CheckConstraint("kind IN ('call', 'text', 'email', 'meeting')", name="suggestion_kind"),
        sa.CheckConstraint(
            "state IN ('open', 'confirmed', 'dismissed', 'already_logged')",
            name="suggestion_state",
        ),
    )

    op.alter_column("jobs", "message_id", nullable=True)
    op.add_column("jobs", sa.Column("note_id", sa.Uuid(), sa.ForeignKey("notes.id"), nullable=True))
    op.add_column(
        "jobs",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_unique_constraint("jobs_note_id_kind_key", "jobs", ["note_id", "kind"])
    op.create_check_constraint("job_subject", "jobs", "(message_id IS NULL) <> (note_id IS NULL)")

    op.add_column(
        "model_calls", sa.Column("note_id", sa.Uuid(), sa.ForeignKey("notes.id"), nullable=True)
    )
    op.add_column("followups", sa.Column("moved_at", sa.DateTime(timezone=True), nullable=True))
    # A follow-up already moved past its first step has no record of when. The upgrade time
    # is the safe stand-in: only contacts after it can close the newer step. A UTC date can
    # run a day behind a team's local one east of UTC, which errs the same safe way.
    op.execute(
        "UPDATE followups SET moved_at = now() "
        "WHERE state = 'open' AND due_on > (created_at AT TIME ZONE 'UTC')::date"
    )


def downgrade() -> None:
    op.drop_column("followups", "moved_at")
    op.drop_column("model_calls", "note_id")
    op.drop_constraint("job_subject", "jobs", type_="check")
    op.drop_constraint("jobs_note_id_kind_key", "jobs", type_="unique")
    op.drop_column("jobs", "created_at")
    op.drop_column("jobs", "note_id")
    op.alter_column("jobs", "message_id", nullable=False)
    op.drop_table("note_suggestions")
    op.drop_column("lead_profiles", "model_from")
    op.drop_constraint("note_read_state", "notes", type_="check")
    for name in ("read_at", "model_answer", "clean_note", "read_reason", "read_state"):
        op.drop_column("notes", name)
