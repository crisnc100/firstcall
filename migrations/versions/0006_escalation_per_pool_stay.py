"""Escalation per pool stay: a lead that re-enters a pool can escalate again

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("escalations", sa.Column("stay", sa.DateTime(timezone=True), nullable=True))
    # Rows fired before this measured from arrival, so arrival is the stay they belong to.
    op.execute(
        "UPDATE escalations SET stay = leads.arrival_at FROM leads WHERE leads.id = escalations.lead_id"
    )
    op.alter_column("escalations", "stay", nullable=False)
    op.drop_constraint("escalations_lead_id_level_key", "escalations", type_="unique")
    op.create_unique_constraint(
        "escalations_lead_id_level_stay_key", "escalations", ["lead_id", "level", "stay"]
    )


def downgrade() -> None:
    op.drop_constraint("escalations_lead_id_level_stay_key", "escalations", type_="unique")
    op.execute(
        "DELETE FROM escalations a USING escalations b "
        "WHERE a.lead_id = b.lead_id AND a.level = b.level AND a.fired_at > b.fired_at"
    )
    op.create_unique_constraint(
        "escalations_lead_id_level_key", "escalations", ["lead_id", "level"]
    )
    op.drop_column("escalations", "stay")
