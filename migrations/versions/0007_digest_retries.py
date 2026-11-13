"""Digest retries: count failed sends, wait before the next, give up as failed

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column("email_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("alerts", sa.Column("email_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("alert_email_state", "alerts", type_="check")
    op.create_check_constraint(
        "alert_email_state", "alerts", "email_state IN ('pending', 'sent', 'refused', 'failed')"
    )


def downgrade() -> None:
    op.execute("UPDATE alerts SET email_state = 'pending' WHERE email_state = 'failed'")
    op.drop_constraint("alert_email_state", "alerts", type_="check")
    op.create_check_constraint(
        "alert_email_state", "alerts", "email_state IN ('pending', 'sent', 'refused')"
    )
    op.drop_column("alerts", "email_retry_at")
    op.drop_column("alerts", "email_attempts")
