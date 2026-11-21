"""Voice notes: a note's source, the transcribing read state, and audio waiting for it.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notes", sa.Column("source", sa.String(8), nullable=False, server_default="typed")
    )
    op.create_check_constraint("note_source", "notes", "source IN ('typed', 'voice')")
    op.drop_constraint("note_read_state", "notes", type_="check")
    op.create_check_constraint(
        "note_read_state",
        "notes",
        "read_state IN ('transcribing', 'pending', 'read', 'needs_model', 'failed')",
    )
    op.create_table(
        "voice_audio",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("orgs.id"), nullable=False, index=True),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id"), nullable=False, index=True),
        sa.Column("note_id", sa.Uuid(), sa.ForeignKey("notes.id"), nullable=False, unique=True),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("seconds", sa.Numeric(6, 2), nullable=False),
        sa.Column("audio", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("format IN ('webm', 'mp4', 'wav', 'mp3', 'ogg')", name="audio_format"),
    )


def downgrade() -> None:
    op.drop_table("voice_audio")
    # A memo still waiting loses its audio with the table, so its note fails as an expired
    # one does.
    op.execute(
        "UPDATE notes SET read_state = 'failed', read_reason = 'not_transcribed' "
        "WHERE read_state = 'transcribing'"
    )
    # The old worker would take a transcription as a note read.
    op.execute(
        "UPDATE jobs SET state = 'failed', lease_until = NULL, last_error = 'not_transcribed' "
        "WHERE kind = 'transcribe' AND state IN ('queued', 'running')"
    )
    op.drop_constraint("note_read_state", "notes", type_="check")
    op.create_check_constraint(
        "note_read_state", "notes", "read_state IN ('pending', 'read', 'needs_model', 'failed')"
    )
    op.drop_constraint("note_source", "notes", type_="check")
    op.drop_column("notes", "source")
