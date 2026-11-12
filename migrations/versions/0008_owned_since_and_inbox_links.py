"""The current owner's start, and the in-app links an inbox email may offer

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("owned_since", sa.DateTime(timezone=True), nullable=True))
    # The best earlier answer for an owned lead is its first claim.
    op.execute(
        "UPDATE leads SET owned_since = claimed_at"
        " WHERE state IN ('owned', 'nurture', 'won', 'lost') AND owner_id IS NOT NULL"
    )
    op.add_column(
        "inbox_emails",
        sa.Column("links", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("inbox_emails", "links")
    op.drop_column("leads", "owned_since")
