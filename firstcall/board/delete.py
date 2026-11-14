"""Delete a lead (admins only).

The lead row and its events stay, emptied of the buyer: the events are the outbox a CRM reads
in sequence, and they carry only ids and codes. `lead.deleted` takes the next number. Message
rows stay too, emptied, so their delivery keys and body hashes still turn a provider's retry
into a duplicate instead of a new lead. Everything else about the lead is deleted.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Text, delete, select, update
from sqlalchemy.orm import Session

from firstcall import events
from firstcall.board.leads import Done, NotFoundError
from firstcall.core.access import Viewer
from firstcall.core.clock import Clock
from firstcall.db import (
    Activity,
    Alert,
    Contact,
    Escalation,
    Followup,
    Identity,
    InboxEmail,
    Job,
    Lead,
    LeadProfile,
    Message,
    Note,
    Outcome,
    PushSend,
)
from firstcall.locks import lock_all

# Tables whose rows for the lead are deleted outright, children before parents.
DELETED = (
    PushSend, Alert, Escalation, Followup, Contact, Note, LeadProfile, Outcome, Activity, Identity,
)  # fmt: skip
# Message columns cleared; the delivery key and hashes stay. An emptied message can never be
# read again, so it leaves the reread queue instead of holding a place at its head.
MESSAGE_SCRUB = {"raw": b"", "extracted": None, "model_answer": None, "source_time": None,
                 "category": None, "needs_reread": False}  # fmt: skip
LEAD_SCRUB = {"name": None, "phone": None, "email": None, "item": None,
              "possible_duplicates": [], "edited_fields": [], "flags": []}  # fmt: skip


def delete_lead(session: Session, viewer: Viewer, lead_id: uuid.UUID, clock: Clock) -> Done:
    if not viewer.admin:
        raise NotFoundError
    keys = session.execute(
        select(Identity.org_id, Identity.kind, Identity.value).where(Identity.lead_id == lead_id)
    ).all()
    # The same advisory locks matching takes, before the lead row, so a message being matched
    # either attaches before the delete or reads its candidates after it.
    lock_all(session, [("identity", str(org), kind, value) for org, kind, value in keys])
    lead = session.get(Lead, lead_id, with_for_update=True)
    if lead is None or str(lead.org_id) != viewer.org_id or lead.deleted_at is not None:
        raise NotFoundError
    now = clock.now()
    # Marked first: the outcomes trigger allows a delete only for a deleted lead.
    lead.deleted_at = now
    for name, value in LEAD_SCRUB.items():
        setattr(lead, name, value)
    session.flush()

    message_ids = list(session.scalars(select(Message.id).where(Message.lead_id == lead.id)))
    alert_ids = select(Alert.id).where(Alert.lead_id == lead.id)
    session.execute(delete(PushSend).where(PushSend.alert_id.in_(alert_ids)))
    for table in DELETED[1:]:
        session.execute(delete(table).where(table.lead_id == lead.id))
    session.execute(delete(Job).where(Job.message_id.in_(message_ids)))
    session.execute(update(Message).where(Message.lead_id == lead.id).values(**MESSAGE_SCRUB))

    # An inbox email naming this lead goes, even a digest that also names others: those
    # leads are still on the board.
    path = f"/leads/{lead.id}"
    mentions = select(InboxEmail).where(
        InboxEmail.org_id == lead.org_id, InboxEmail.links.cast(Text).contains(path)
    )
    for email in session.scalars(mentions):
        if any(link.get("path") == path for link in email.links):
            session.delete(email)
    flagged = select(Lead).where(
        Lead.org_id == lead.org_id,
        Lead.id != lead.id,
        Lead.possible_duplicates.cast(Text).contains(str(lead.id)),
    )
    for other in session.scalars(flagged.order_by(Lead.id).with_for_update()):
        other.possible_duplicates = [d for d in other.possible_duplicates if d != str(lead.id)]

    payload = {"by": viewer.user_id}
    events.write(session, lead, "lead.deleted", f"delete:{lead.id}", payload, now)
    return Done(lead.id, "lead.deleted")
