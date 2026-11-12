"""Source allowed origins

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("allowed_origins", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.alter_column("sources", "allowed_origins", server_default=None)


def downgrade() -> None:
    op.drop_column("sources", "allowed_origins")
