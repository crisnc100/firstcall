"""The transcriber's jobs, and the expiry that bounds how long audio waits.

A transcription runs in three steps, never one long transaction:
1. Read the audio; commit.
2. Transcribe, with no transaction open, while the lease thread renews the lease and beats.
3. Lock the lead, then the note (delete lead's order), then the job. Write only if this
   attempt still holds the job and the note still waits; otherwise drop the transcript.
   Then fill the note, set it `pending`, queue its read, delete the audio, finish the job.

Audio that will never transcribe fails the note at once. Any other error retries, and out of
attempts the note fails too; either way the audio is deleted (`pipeline.jobs._fail`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import sessionmaker

from firstcall.board.notes import READ_NOTE_JOB
from firstcall.core.clock import Clock
from firstcall.db import Job, Lead, Note, VoiceAudio
from firstcall.errors import log_failure
from firstcall.pipeline.jobs import (
    NOT_TRANSCRIBED,
    TRANSCRIBE,
    Claimed,
    current,
    keep_lease,
    retry_or_fail,
)
from firstcall.voice.transcribe import Transcriber, UnusableAudioError, note_text

log = logging.getLogger("firstcall.voice")
# A memo still waiting this long after upload fails and its audio is deleted, so audio
# never sits in the database while no transcriber runs.
EXPIRY = timedelta(minutes=30)


def run_transcription(
    sessions: sessionmaker[Any],
    clock: Clock,
    claimed: Claimed,
    lease_seconds: float,
    max_attempts: int,
    transcriber: Transcriber,
    on_renew: Callable[[], None] | None = None,
) -> str | None:
    """Transcribe a claimed job. Returns transcribed, failed, skipped (this attempt no longer
    holds the job, or the note no longer waits), or None after an error that will retry."""
    try:
        with keep_lease(sessions, clock, claimed, lease_seconds, on_renew):
            with sessions.begin() as session:
                if current(session, claimed) is None:
                    return "skipped"
                found = session.execute(
                    select(VoiceAudio.audio, VoiceAudio.format).where(
                        VoiceAudio.note_id == claimed.note_id
                    )
                ).one_or_none()
            if found is None:
                raise UnusableAudioError("the audio is gone")
            text = note_text(transcriber.transcribe(found.audio, found.format))
            return _finish(sessions, claimed, text)
    except UnusableAudioError as error:
        log.warning("job %s: memo not transcribed: %s", claimed.job_id, error)
        retry_or_fail(sessions, clock, claimed, error, max_attempts, give_up=True)
        return "failed"
    except Exception as error:
        log_failure(log, error, "job %s failed on attempt %s", claimed.job_id, claimed.attempt)
        retry_or_fail(sessions, clock, claimed, error, max_attempts)
        return None


def _finish(sessions: sessionmaker[Any], claimed: Claimed, text: str) -> str:
    with sessions.begin() as session:
        lead_id = session.scalar(select(Note.lead_id).where(Note.id == claimed.note_id))
        if lead_id is None:
            return "skipped"
        lead = session.get(Lead, lead_id, with_for_update=True, populate_existing=True)
        note = session.scalar(
            select(Note)
            .where(Note.id == claimed.note_id, Note.read_state == "transcribing")
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        job = current(session, claimed)
        if note is None or job is None or lead is None or lead.deleted_at is not None:
            return "skipped"
        note.text = text
        note.read_state = "pending"
        session.add(Job(org_id=note.org_id, note_id=note.id, kind=READ_NOTE_JOB))
        session.execute(delete(VoiceAudio).where(VoiceAudio.note_id == note.id))
        job.state = "done"
        job.lease_until = None
        return "transcribed"


def expire_waiting(sessions: sessionmaker[Any], clock: Clock) -> int:
    """Fail every memo still transcribing EXPIRY after its upload, delete its audio and end
    its job. The scheduler runs this, so it happens even when no transcriber runs."""
    cutoff = clock.now() - EXPIRY
    with sessions() as session:
        waiting = session.execute(
            select(Note.id, Note.lead_id)
            .where(Note.read_state == "transcribing", Note.created_at < cutoff)
            .order_by(Note.created_at, Note.id)
        ).all()
    expired = 0
    for note_id, lead_id in waiting:
        with sessions.begin() as session:
            session.get(Lead, lead_id, with_for_update=True)
            note = session.scalar(
                select(Note)
                .where(Note.id == note_id, Note.read_state == "transcribing")
                .with_for_update()
            )
            if note is None:
                continue
            note.read_state, note.read_reason = "failed", NOT_TRANSCRIBED
            session.execute(delete(VoiceAudio).where(VoiceAudio.note_id == note_id))
            session.execute(
                update(Job)
                .where(
                    Job.note_id == note_id,
                    Job.kind == TRANSCRIBE,
                    Job.state.in_(("queued", "running")),
                )
                .values(state="failed", lease_until=None, last_error="expired")
            )
            expired += 1
    return expired
