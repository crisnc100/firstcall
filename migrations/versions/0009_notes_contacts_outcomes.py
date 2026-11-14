"""Notes, contacts, the buyer profile and outcomes; first contact, the contact-due clock and
delete on leads

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("orgs.id"), nullable=False, index=True),
    ]


def upgrade() -> None:
    for name, kind in (
        ("first_contact_at", sa.DateTime(timezone=True)),
        ("first_contact_minutes", sa.Numeric(12, 2)),
        ("first_contact_business_minutes", sa.Numeric(12, 2)),
        ("uncontacted_since", sa.DateTime(timezone=True)),
        ("deleted_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("leads", sa.Column(name, kind, nullable=True))
    # Leads owned before this migration start their uncontacted stretch at their owner's start.
    op.execute(
        "UPDATE leads SET uncontacted_since = owned_since"
        " WHERE state IN ('owned', 'nurture') AND owned_since IS NOT NULL"
    )

    op.create_table(
        "notes",
        *_base(),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id"), nullable=False, index=True),
        sa.Column("author_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "contacts",
        *_base(),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id"), nullable=False, index=True),
        sa.Column("note_id", sa.Uuid(), sa.ForeignKey("notes.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("made_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("logged_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("note_id", "kind"),
        sa.CheckConstraint("kind IN ('call', 'text', 'email', 'meeting')", name="contact_kind"),
        sa.CheckConstraint("source IN ('typed', 'suggestion')", name="contact_source"),
    )
    op.create_table(
        "lead_profiles",
        *_base(),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id"), nullable=False, unique=True),
        sa.Column("budget_text", sa.Text(), nullable=True),
        sa.Column("budget_min", sa.BigInteger(), nullable=True),
        sa.Column("budget_max", sa.BigInteger(), nullable=True),
        sa.Column("authority", sa.String(32), nullable=False),
        sa.Column("authority_others", sa.Text(), nullable=True),
        sa.Column("need", sa.Text(), nullable=True),
        sa.Column("timeline_text", sa.Text(), nullable=True),
        sa.Column("timeline_month", sa.String(7), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("person_fields", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "outcomes",
        *_base(),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id"), nullable=False, index=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(32), nullable=True),
        sa.Column("reason_text", sa.Text(), nullable=True),
        sa.Column("competitor", sa.String(200), nullable=True),
        sa.Column("by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("outcome IN ('won', 'lost', 'not_real', 'nurture')", name="outcome"),
    )
    # Outcome history is append-only. Only delete lead may remove rows, and only after it has
    # marked the lead deleted in the same transaction.
    op.execute(
        """
        CREATE FUNCTION outcomes_append_only() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' AND EXISTS (
            SELECT 1 FROM leads WHERE id = OLD.lead_id AND deleted_at IS NOT NULL
          ) THEN
            RETURN OLD;
          END IF;
          RAISE EXCEPTION 'outcomes are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER outcomes_append_only BEFORE UPDATE OR DELETE ON outcomes"
        " FOR EACH ROW EXECUTE FUNCTION outcomes_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER outcomes_append_only ON outcomes")
    op.execute("DROP FUNCTION outcomes_append_only()")
    for table in ("outcomes", "lead_profiles", "contacts", "notes"):
        op.drop_table(table)
    for name in (
        "deleted_at",
        "uncontacted_since",
        "first_contact_business_minutes",
        "first_contact_minutes",
        "first_contact_at",
    ):
        op.drop_column("leads", name)
