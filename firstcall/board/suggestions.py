"""A note's model answer applied to its lead, and the people who confirm or dismiss what it
suggests.

A suggestion logs nothing on its own. Confirm logs one contact, dated when the note was
written and made by its author; a kind the note already logged is closed as already logged.
The profile takes the model's values field by field: never a person-set field, never an
empty value, and never over a value from a newer note.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from firstcall.board import notes
from firstcall.board.leads import ConflictError, Done, NotFoundError, ref
from firstcall.core.access import Viewer
from firstcall.core.clock import Clock
from firstcall.core.note_read import NoteAnswer
from firstcall.core.note_terms import EMPTY_AUTHORITY
from firstcall.core.outcomes import may_log_contact, may_note
from firstcall.db import Contact, Lead, LeadProfile, Note, NoteSuggestion


def _model_values(answer: NoteAnswer) -> dict[str, Any]:
    budget = answer.budget
    return {
        "budget_text": budget.text if budget else None,
        "budget_min": budget.min if budget else None,
        "budget_max": budget.max if budget else None,
        "authority": None if answer.authority == EMPTY_AUTHORITY else answer.authority,
        "authority_others": answer.authority_others,
        "need": answer.need,
        "timeline_text": answer.timeline_text,
        "timeline_month": answer.timeline_month,
    }


def merge_profile(
    session: Session, lead: Lead, note: Note, answer: NoteAnswer, now: datetime
) -> None:
    row = notes.profile(session, lead)
    if row is None:
        row = LeadProfile(
            id=uuid.uuid4(), org_id=lead.org_id, lead_id=lead.id, authority=EMPTY_AUTHORITY,
            person_fields=[], model_from={}, updated_at=now,
        )  # fmt: skip
        session.add(row)
    person = set(row.person_fields)
    sources = dict(row.model_from or {})
    written = note.created_at.isoformat()
    for name, value in _model_values(answer).items():
        if value is None or name in person:
            continue
        earlier = sources.get(name)
        if earlier is not None and datetime.fromisoformat(earlier) > note.created_at:
            continue
        setattr(row, name, value)
        sources[name] = written
    if answer.notes and "notes" not in person:
        row.notes = f"{row.notes}\n{answer.notes}" if row.notes else answer.notes
    row.model_from = sources
    row.updated_at = now


def apply_answer(
    session: Session, lead: Lead, note: Note, answer: NoteAnswer, now: datetime
) -> None:
    """Store the answer on the note, open its suggestions and merge the profile. The caller
    holds the lead's row lock."""
    note.read_state, note.read_reason, note.read_at = "read", None, now
    note.clean_note = answer.clean_note
    note.model_answer = answer.as_json()
    logged = set(session.scalars(select(Contact.kind).where(Contact.note_id == note.id)))
    for action in answer.actions:
        already = action.kind in logged
        session.execute(
            insert(NoteSuggestion)
            .values(
                id=uuid.uuid4(),
                org_id=note.org_id,
                lead_id=lead.id,
                note_id=note.id,
                kind=action.kind,
                quote=action.quote,
                state="already_logged" if already else "open",
                closed_at=now if already else None,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["note_id", "kind"])
        )
    merge_profile(session, lead, note, answer, now)


def _open(
    session: Session, viewer: Viewer, lead_id: uuid.UUID, suggestion_id: uuid.UUID
) -> tuple[Lead, NoteSuggestion]:
    lead = notes.locked_lead(session, viewer, lead_id)
    if not may_note(viewer, ref(lead)):
        raise NotFoundError
    found = session.scalar(
        select(NoteSuggestion)
        .where(NoteSuggestion.id == suggestion_id, NoteSuggestion.lead_id == lead.id)
        .with_for_update()
    )
    if found is None:
        raise NotFoundError
    if found.state != "open":
        raise ConflictError("this suggestion was already closed")
    return lead, found


def confirm(
    session: Session, viewer: Viewer, lead_id: uuid.UUID, suggestion_id: uuid.UUID, clock: Clock
) -> Done:
    lead, found = _open(session, viewer, lead_id, suggestion_id)
    if not may_log_contact(viewer, ref(lead)):
        raise ConflictError("a contact can be confirmed once the lead is owned or in nurture")
    note = session.get_one(Note, found.note_id)
    contact = notes.log_contact(
        session, lead, note, found.kind, note.author_id, uuid.UUID(viewer.user_id),
        "suggestion", note.created_at,
    )  # fmt: skip
    found.state = "confirmed" if contact is not None else "already_logged"
    found.closed_by, found.closed_at = uuid.UUID(viewer.user_id), clock.now()
    return Done(lead.id, "contact.logged" if contact is not None else "suggestion.closed")


def dismiss(
    session: Session, viewer: Viewer, lead_id: uuid.UUID, suggestion_id: uuid.UUID, clock: Clock
) -> Done:
    lead, found = _open(session, viewer, lead_id, suggestion_id)
    found.state = "dismissed"
    found.closed_by, found.closed_at = uuid.UUID(viewer.user_id), clock.now()
    return Done(lead.id, "suggestion.dismissed")
