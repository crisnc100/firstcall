"""Reread errors: how many rereads of a message raised

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("reread_errors", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.alter_column("messages", "reread_errors", server_default=None)


def downgrade() -> None:
    op.drop_column("messages", "reread_errors")
