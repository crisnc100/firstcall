"""Reading notes: one structured-output call per note, through the gateway, from a note job
or the reread pass.

Only the note's text goes in the user message; its date and the team's time zone go in as
context, so the model can turn "next spring" into a month. An answer is stored on the note
and applied by `board.suggestions.apply_answer`. With no model the note is marked
`needs_model` and the reread pass reads it once a model answers; an answer that does not
parse marks it `failed`, so it is never paid for again.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import suggestions
from firstcall.board.leads import team_hours
from firstcall.core.clock import Clock
from firstcall.core.note_read import NoteAnswer, answer_schema, parse_note_answer
from firstcall.db import Lead, Note, Team
from firstcall.model.gateway import Gateway, ModelRequest, ModelUnavailableError
from firstcall.model.prompts import READ_NOTE

UNREADABLE = "unreadable"
BATCH = 50
# A note's date is read in this zone when its lead has no team, or the team's zone does not
# read.
FALLBACK_ZONE = "UTC"


@dataclass(frozen=True)
class NoteOutcome:
    answer: NoteAnswer | None
    # Why there is no answer: a gateway reason, or UNREADABLE.
    reason: str | None = None


def context_text(note_date: str, zone: str) -> str:
    return f"Note date: {note_date}\nTime zone: {zone}"


class NoteReader:
    def __init__(self, gateway: Gateway, detached: bool = False) -> None:
        self.gateway = gateway
        # A detached reader reads note scripts that are never stored (the eval), so its
        # calls are logged without a note id.
        self.is_detached = detached

    def request(
        self, org_id: uuid.UUID, note_id: uuid.UUID | None, text: str, note_date: str, zone: str
    ) -> ModelRequest:
        return ModelRequest(
            org_id=org_id,
            message_id=None,
            task=READ_NOTE,
            intake_text=text,
            context=context_text(note_date, zone),
            schema=answer_schema(),
            raw=text.encode(),
            note_id=None if self.is_detached else note_id,
        )

    def read(
        self, org_id: uuid.UUID, note_id: uuid.UUID | None, text: str, note_date: str, zone: str
    ) -> NoteOutcome:
        try:
            reply = self.gateway.call(self.request(org_id, note_id, text, note_date, zone))
        except ModelUnavailableError as error:
            return NoteOutcome(None, error.reason)
        answer = parse_note_answer(reply.content, text)
        return NoteOutcome(answer, None if answer else UNREADABLE)


def _zone(session: Session, lead_id: uuid.UUID) -> str:
    team_id = session.scalar(select(Lead.team_id).where(Lead.id == lead_id))
    team = session.get(Team, team_id) if team_id else None
    if team is None:
        return FALLBACK_ZONE
    try:
        return team_hours(team).zone.key
    except (ValueError, KeyError, TypeError):
        return FALLBACK_ZONE


def _read(
    session: Session, note_id: uuid.UUID, waiting: str, reader: NoteReader | None, clock: Clock
) -> str:
    """Read one note that is still in the `waiting` state. The model is asked before any
    lock is taken, so a slow answer never holds the lead; the answer is applied under the
    lead's row lock, and only if the note is still waiting and its lead not deleted.
    Returns read, failed, skipped or the reason no model answered."""
    note = session.scalar(select(Note).where(Note.id == note_id, Note.read_state == waiting))
    if note is None:
        return "skipped"
    if reader is None:
        outcome = NoteOutcome(None, "offline")
    else:
        zone = _zone(session, note.lead_id)
        note_date = note.created_at.astimezone(ZoneInfo(zone)).date().isoformat()
        outcome = reader.read(note.org_id, note.id, note.text, note_date, zone)
    lead = session.get(Lead, note.lead_id, with_for_update=True, populate_existing=True)
    note = session.scalar(
        select(Note)
        .where(Note.id == note_id, Note.read_state == waiting)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if note is None or lead is None or lead.deleted_at is not None:
        return "skipped"
    if outcome.answer is not None:
        suggestions.apply_answer(session, lead, note, outcome.answer, clock.now())
        return "read"
    if outcome.reason == UNREADABLE:
        note.read_state, note.read_reason = "failed", UNREADABLE
        return "failed"
    note.read_state, note.read_reason = "needs_model", outcome.reason
    return outcome.reason or "needs_model"


def read_note_job(
    session: Session, note_id: uuid.UUID, clock: Clock, reader: NoteReader | None
) -> str:
    """A note job. Without a reader (an offline worker) the note waits for the reread
    pass."""
    return _read(session, note_id, "pending", reader, clock)


def reread_notes(
    sessions: sessionmaker[Any], reader: NoteReader, limit: int = BATCH
) -> dict[str, int]:
    """Read up to `limit` notes that are waiting for a model, oldest first. Stops at the
    first sign the model is still unavailable."""
    done: dict[str, int] = {}
    with sessions() as session:
        waiting = list(
            session.scalars(
                select(Note.id)
                .where(Note.read_state == "needs_model")
                .order_by(Note.created_at, Note.id)
                .limit(limit)
            )
        )
    for note_id in waiting:
        with sessions.begin() as session:
            result = _read(session, note_id, "needs_model", reader, reader.gateway.clock)
        if result == "skipped":
            continue
        if result not in ("read", "failed"):
            done["halted"] = done.get("halted", 0) + 1
            break
        done[result] = done.get(result, 0) + 1
    return done
