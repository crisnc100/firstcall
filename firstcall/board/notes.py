"""Notes, real contacts and the buyer profile.

A note is typed, or recorded as a voice memo that the transcriber fills in later. Either may
carry an action (call, text, email or meeting). Choosing one is the person's confirmation
that the contact happened, so it logs a real contact at once, before any transcript. A note
without an action is not a touch. Every change writes its lead event in the caller's
transaction.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from firstcall import events
from firstcall.board import followups
from firstcall.board.leads import InvalidError, NotFoundError, ref, team_hours
from firstcall.core.access import Viewer
from firstcall.core.clock import Clock
from firstcall.core.hours import business_minutes, wall_minutes
from firstcall.core.note_terms import ACTION_KINDS, AUTHORITIES
from firstcall.core.outcomes import may_log_contact, may_note
from firstcall.db import Contact, Job, Lead, LeadProfile, Note, Team, VoiceAudio
from firstcall.locks import lock_all

log = logging.getLogger("firstcall.board")

NOTE_MAX = 5000
READ_NOTE_JOB = "read_note"
TRANSCRIBE_JOB = "transcribe"
# Memos waiting for the transcriber: at most this many per person, and this much audio per
# org, so one person cannot queue an hour of CPU.
USER_WAITING = 3
ORG_WAITING_SECONDS = 600
PROFILE_FIELDS = (
    "budget_text",
    "budget_min",
    "budget_max",
    "authority",
    "authority_others",
    "need",
    "timeline_text",
    "timeline_month",
    "notes",
)


@dataclass(frozen=True)
class Added:
    note_id: uuid.UUID
    contact_id: uuid.UUID | None


class TooManyError(Exception):
    """The person has USER_WAITING memos waiting already."""


class BusyError(Exception):
    """The org's waiting memos hold ORG_WAITING_SECONDS of audio already."""


def locked_lead(session: Session, viewer: Viewer, lead_id: uuid.UUID) -> Lead:
    lead = session.get(Lead, lead_id, with_for_update=True)
    if lead is None or str(lead.org_id) != viewer.org_id or lead.deleted_at is not None:
        raise NotFoundError
    return lead


def add_note(
    session: Session,
    viewer: Viewer,
    lead_id: uuid.UUID,
    text: str,
    action: str | None,
    clock: Clock,
) -> Added:
    lead = locked_lead(session, viewer, lead_id)
    if not may_note(viewer, ref(lead)):
        raise NotFoundError
    text = text.strip()
    if not text or len(text) > NOTE_MAX:
        raise InvalidError(f"a note needs between 1 and {NOTE_MAX} characters")
    if action is not None and action not in ACTION_KINDS:
        raise InvalidError(f"the action must be one of {ACTION_KINDS}")
    if action is not None and not may_log_contact(viewer, ref(lead)):
        raise InvalidError("a contact can be logged only on an owned or nurture lead")
    now = clock.now()
    user_id = uuid.UUID(viewer.user_id)
    note = Note(
        id=uuid.uuid4(), org_id=lead.org_id, lead_id=lead.id, author_id=user_id, text=text,
        created_at=now, source="typed",
    )  # fmt: skip
    session.add(note)
    session.flush()
    # The model reads the note in its own job, after this commits, so saving a note never
    # waits on a model.
    session.add(Job(org_id=lead.org_id, note_id=note.id, kind=READ_NOTE_JOB))
    return Added(note.id, _added(session, lead, note, action, now))


def _added(
    session: Session, lead: Lead, note: Note, action: str | None, now: datetime
) -> uuid.UUID | None:
    """The note's event and, if the person picked an action, its contact."""
    payload = {"note_id": str(note.id), "by": str(note.author_id), "source": note.source}
    events.write(session, lead, "note.added", f"note:{note.id}", payload, now)
    if action is None:
        return None
    author = note.author_id
    return log_contact(session, lead, note, action, author, author, "typed", now)


def voice_lead(
    session: Session, viewer: Viewer, lead_id: uuid.UUID, action: str | None, lock: bool
) -> Lead:
    """The lead a memo may be recorded on with this action, as add_note checks a typed note.
    The api asks first without a lock, before reading the audio, and again on saving."""
    lead = session.get(Lead, lead_id, with_for_update=lock)
    if lead is None or str(lead.org_id) != viewer.org_id or lead.deleted_at is not None:
        raise NotFoundError
    if not may_note(viewer, ref(lead)):
        raise NotFoundError
    if action is not None and action not in ACTION_KINDS:
        raise InvalidError(f"the action must be one of {ACTION_KINDS}")
    if action is not None and not may_log_contact(viewer, ref(lead)):
        raise InvalidError("a contact can be logged only on an owned or nurture lead")
    return lead


def check_waiting(session: Session, viewer: Viewer) -> None:
    """TooManyError when the person already has USER_WAITING memos waiting, BusyError when
    the org's waiting memos hold ORG_WAITING_SECONDS of audio. The api asks first, without
    a lock, so a memo over a cap is refused before its audio is read; saving asks again
    under the org's lock."""
    waiting = select(func.count()).select_from(VoiceAudio).join(Note, Note.id == VoiceAudio.note_id)
    mine = session.scalar(waiting.where(Note.author_id == uuid.UUID(viewer.user_id))) or 0
    if mine >= USER_WAITING:
        raise TooManyError
    queued = session.scalar(
        select(func.coalesce(func.sum(VoiceAudio.seconds), 0)).where(
            VoiceAudio.org_id == uuid.UUID(viewer.org_id)
        )
    )
    if float(queued or 0) >= ORG_WAITING_SECONDS:
        raise BusyError


def add_voice_note(
    session: Session,
    viewer: Viewer,
    lead_id: uuid.UUID,
    audio: bytes,
    audio_format: str,
    seconds: float,
    action: str | None,
    clock: Clock,
) -> Added:
    """An empty voice note waiting to be transcribed, its audio and its transcribe job. A
    picked action logs its contact now: the person's pick stands whatever the transcript."""
    lead = voice_lead(session, viewer, lead_id, action, lock=True)
    # One org's uploads count their waiting memos one at a time, after the lead's lock.
    lock_all(session, [("voice", str(lead.org_id))])
    check_waiting(session, viewer)
    user_id = uuid.UUID(viewer.user_id)
    now = clock.now()
    note = Note(
        id=uuid.uuid4(), org_id=lead.org_id, lead_id=lead.id, author_id=user_id, text="",
        created_at=now, source="voice", read_state="transcribing",
    )  # fmt: skip
    session.add(note)
    session.flush()
    session.add(
        VoiceAudio(
            org_id=lead.org_id, lead_id=lead.id, note_id=note.id, format=audio_format,
            seconds=Decimal(str(seconds)), audio=audio, created_at=now,
        )
    )  # fmt: skip
    session.add(Job(org_id=lead.org_id, note_id=note.id, kind=TRANSCRIBE_JOB))
    return Added(note.id, _added(session, lead, note, action, now))


def log_contact(
    session: Session,
    lead: Lead,
    note: Note,
    kind: str,
    made_by: uuid.UUID,
    logged_by: uuid.UUID,
    source: str,
    at: datetime,
) -> uuid.UUID | None:
    """Log one contact of this kind from this note; None when the note already logged one.
    The caller holds the lead's row lock and has checked who may log it."""
    contact_id = session.scalar(
        insert(Contact)
        .values(
            id=uuid.uuid4(),
            org_id=lead.org_id,
            lead_id=lead.id,
            note_id=note.id,
            kind=kind,
            made_by=made_by,
            logged_by=logged_by,
            source=source,
            at=at,
        )
        .on_conflict_do_nothing(index_elements=["note_id", "kind"])
        .returning(Contact.id)
    )
    if contact_id is None:
        return None
    _first_contact(session, lead, at)
    if lead.uncontacted_since is not None and at >= lead.uncontacted_since:
        lead.uncontacted_since = None
    payload = {
        "contact_id": str(contact_id),
        "note_id": str(note.id),
        "kind": kind,
        "by": str(made_by),
        "logged_by": str(logged_by),
        "source": source,
    }
    events.write(session, lead, "contact.logged", f"contact:{contact_id}", payload, at)
    followups.touched(session, lead, at)
    return contact_id


def _first_contact(session: Session, lead: Lead, at: datetime) -> None:
    """Time to first real contact is the earliest contact on the lead, so a contact dated
    before the stored one (a suggestion confirmed late) moves it back."""
    if lead.first_contact_at is not None and at >= lead.first_contact_at:
        return
    lead.first_contact_at = max(at, lead.arrival_at)
    first_contact_times(session, lead)


def first_contact_times(session: Session, lead: Lead) -> None:
    """The wall and business minutes from arrival to the first contact, if there was one."""
    at = lead.first_contact_at
    if at is None:
        return
    lead.first_contact_minutes = _minutes(wall_minutes(lead.arrival_at, at))
    lead.first_contact_business_minutes = None
    team = session.get(Team, lead.team_id) if lead.team_id else None
    if team is None:
        return
    try:
        hours = team_hours(team)
    except (ValueError, KeyError, TypeError) as error:
        log.error("team %s hours do not read; first contact kept without business minutes: %s",
                  team.slug, error)  # fmt: skip
        return
    lead.first_contact_business_minutes = _minutes(business_minutes(lead.arrival_at, at, hours))


def _minutes(value: float) -> Decimal:
    return Decimal(str(round(value, 2)))


def profile(session: Session, lead: Lead) -> LeadProfile | None:
    return session.scalar(select(LeadProfile).where(LeadProfile.lead_id == lead.id))


def edit_profile(
    session: Session,
    viewer: Viewer,
    lead_id: uuid.UUID,
    changes: dict[str, Any],
    clock: Clock,
) -> LeadProfile:
    """A person's edit. Each field named here becomes person-set, which the model never
    changes."""
    lead = locked_lead(session, viewer, lead_id)
    if not may_note(viewer, ref(lead)):
        raise NotFoundError
    unknown = set(changes) - set(PROFILE_FIELDS)
    if unknown:
        raise InvalidError(f"unknown profile fields: {sorted(unknown)}")
    if "authority" in changes and changes["authority"] not in AUTHORITIES:
        raise InvalidError(f"authority must be one of {AUTHORITIES}")
    month = changes.get("timeline_month")
    if month is not None and not _month(month):
        raise InvalidError("timeline_month is YYYY-MM")
    for bound in ("budget_min", "budget_max"):
        value = changes.get(bound)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                  or value < 0):  # fmt: skip
            raise InvalidError(f"{bound} is a whole number of dollars")
    now = clock.now()
    row = profile(session, lead)
    if row is None:
        row = LeadProfile(
            id=uuid.uuid4(), org_id=lead.org_id, lead_id=lead.id, authority="unknown",
            person_fields=[], updated_at=now,
        )  # fmt: skip
        session.add(row)
    for name, value in changes.items():
        if isinstance(value, str):
            value = value.strip() or None
        if name == "authority" and value is None:
            value = "unknown"
        setattr(row, name, value)
    row.person_fields = sorted(set(row.person_fields) | set(changes))
    row.updated_at = now
    return row


def _month(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 7 or value[4] != "-":
        return False
    year, month = value[:4], value[5:]
    return year.isdigit() and month.isdigit() and 1 <= int(month) <= 12
