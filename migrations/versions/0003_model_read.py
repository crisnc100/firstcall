"""Model read: the model's answer on a message, and person-edited fields and flags on a lead

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("model_answer", sa.JSON(), nullable=True))
    op.add_column("messages", sa.Column("model_unavailable", sa.String(length=32), nullable=True))
    for column in ("edited_fields", "flags"):
        op.add_column(
            "leads",
            sa.Column(column, sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        )
        op.alter_column("leads", column, server_default=None)


def downgrade() -> None:
    op.drop_column("leads", "flags")
    op.drop_column("leads", "edited_fields")
    op.drop_column("messages", "model_unavailable")
    op.drop_column("messages", "model_answer")
