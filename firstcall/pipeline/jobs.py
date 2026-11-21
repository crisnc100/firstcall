"""Jobs in Postgres: claim with a lease, process, retry, reap.

A worker claims a job with FOR UPDATE SKIP LOCKED and holds a lease that it renews while
working. The reaper returns jobs whose lease ran out, so a dead worker's job is picked up
again. After max_attempts tries the message ends `failed: retries_exhausted`, or a note's
read ends `failed` with the same reason, its text and any typed action kept. A voice note
whose transcription runs out of attempts ends `failed: not_transcribed`, its audio deleted
and any picked action kept.

The main worker takes every kind of job but `transcribe`, which only the transcriber takes
(`firstcall.voice.job`), so a memo never holds a new lead's alert behind it.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board.notes import TRANSCRIBE_JOB
from firstcall.core.clock import Clock
from firstcall.db import Job, Message, Note, VoiceAudio
from firstcall.notes.reader import NoteReader, read_note_job
from firstcall.pipeline.model_read import ModelReader
from firstcall.pipeline.process import process

log = logging.getLogger("firstcall.jobs")
EXHAUSTED = "retries_exhausted"
TRANSCRIBE = TRANSCRIBE_JOB
NOT_TRANSCRIBED = "not_transcribed"


@dataclass(frozen=True)
class Claimed:
    job_id: uuid.UUID
    message_id: uuid.UUID | None
    attempt: int
    note_id: uuid.UUID | None = None
    kind: str = "process"


def _fail(session: Session, job: Job, reason: str) -> None:
    job.state = "failed"
    job.lease_until = None
    if job.kind == TRANSCRIBE:
        # However a transcription fails, the note gets the one reason the page explains,
        # and its audio goes.
        session.execute(
            update(Note)
            .where(Note.id == job.note_id, Note.read_state == "transcribing")
            .values(read_state="failed", read_reason=NOT_TRANSCRIBED)
        )
        session.execute(delete(VoiceAudio).where(VoiceAudio.note_id == job.note_id))
        return
    if job.note_id is not None:
        session.execute(
            update(Note)
            .where(Note.id == job.note_id, Note.read_state == "pending")
            .values(read_state="failed", read_reason=reason)
        )
        return
    session.execute(
        update(Message)
        .where(Message.id == job.message_id, Message.status.in_(("received", "processing")))
        .values(status="failed", reason=reason)
    )


def claim(
    sessions: sessionmaker[Any],
    clock: Clock,
    lease_seconds: float,
    max_attempts: int,
    kinds: Collection[str] | None = None,
    skip: Collection[str] = (TRANSCRIBE,),
) -> Claimed | None:
    """The oldest ready job of the given kinds (any kind when None), less those in skip.
    The main worker skips transcription; the transcriber asks for it alone."""
    now = clock.now()
    ready = [Job.state == "queued", or_(Job.run_after.is_(None), Job.run_after <= now)]
    if kinds is not None:
        ready.append(Job.kind.in_(kinds))
    if skip:
        ready.append(Job.kind.not_in(skip))
    with sessions.begin() as session:
        while True:
            job = session.scalar(
                select(Job)
                .where(*ready)
                .order_by(Job.created_at, Job.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            if job.attempts >= max_attempts:
                _fail(session, job, EXHAUSTED)
                continue
            job.state = "running"
            job.attempts += 1
            job.lease_until = now + timedelta(seconds=lease_seconds)
            return Claimed(job.id, job.message_id, job.attempts, job.note_id, job.kind)


@contextmanager
def keep_lease(
    sessions: sessionmaker[Any],
    clock: Clock,
    claimed: Claimed,
    lease_seconds: float,
    on_renew: Callable[[], None] | None = None,
) -> Iterator[None]:
    """Renew the lease every third of its length until the block ends, and call on_renew
    each time (the worker's heartbeat, so a long job never looks dead). A renewal that
    fails is logged and tried again at the next third; one database error must not leave
    the job to the reaper while it still runs."""
    stop = threading.Event()

    def renew() -> None:
        while not stop.wait(lease_seconds / 3):
            try:
                with sessions.begin() as session:
                    session.execute(
                        update(Job)
                        .where(
                            Job.id == claimed.job_id,
                            Job.state == "running",
                            Job.attempts == claimed.attempt,
                        )
                        .values(lease_until=clock.now() + timedelta(seconds=lease_seconds))
                    )
                if on_renew is not None:
                    on_renew()
            except Exception:
                log.exception("renewing the lease of job %s failed", claimed.job_id)

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


def run(
    sessions: sessionmaker[Any],
    clock: Clock,
    claimed: Claimed,
    lease_seconds: float,
    max_attempts: int,
    reader: ModelReader | None = None,
    on_renew: Callable[[], None] | None = None,
) -> str | None:
    """Process a claimed job. The message's terminal status and the job's completion commit
    together; on an error the job goes back in the queue or, out of attempts, fails."""
    if claimed.kind == TRANSCRIBE:
        raise ValueError("transcribe jobs run in the transcriber")
    try:
        with (
            keep_lease(sessions, clock, claimed, lease_seconds, on_renew),
            sessions.begin() as session,
        ):
            if claimed.note_id is not None:
                # A note job takes the lead before its own row, the order delete lead uses,
                # and holds no lock while the model reads. Delete may remove the job meanwhile.
                notes = NoteReader(reader.gateway) if reader is not None else None
                status = read_note_job(session, claimed.note_id, clock, notes)
                job = session.get(Job, claimed.job_id, with_for_update=True)
                if job is None:
                    return status
            else:
                job = session.get_one(Job, claimed.job_id, with_for_update=True)
                assert claimed.message_id is not None
                status = process(session, claimed.message_id, clock, reader)
            job.state = "done"
            job.lease_until = None
            return status
    except Exception as error:
        log.exception("job %s failed on attempt %s", claimed.job_id, claimed.attempt)
        retry_or_fail(sessions, clock, claimed, error, max_attempts)
        return None


def current(session: Session, claimed: Claimed) -> Job | None:
    """The claimed job, locked, while this attempt still holds it: not deleted with its
    lead, not reaped and claimed again, not failed by the voice expiry."""
    job = session.get(Job, claimed.job_id, with_for_update=True, populate_existing=True)
    if job is None or job.state != "running" or job.attempts != claimed.attempt:
        return None
    return job


def retry_or_fail(
    sessions: sessionmaker[Any],
    clock: Clock,
    claimed: Claimed,
    error: Exception,
    max_attempts: int,
    give_up: bool = False,
) -> None:
    """After an error: back in the queue after a backoff, or failed when out of attempts or
    told to give up. An attempt that no longer holds the job changes nothing."""
    with sessions.begin() as session:
        job = current(session, claimed)
        if job is None:
            return
        job.last_error = f"{type(error).__name__}: {error}"[:2000]
        if give_up or job.attempts >= max_attempts:
            _fail(session, job, EXHAUSTED)
        else:
            job.state = "queued"
            job.lease_until = None
            job.run_after = clock.now() + timedelta(seconds=2**job.attempts)


def run_one(
    sessions: sessionmaker[Any],
    clock: Clock,
    lease_seconds: float = 60,
    max_attempts: int = 5,
    reader: ModelReader | None = None,
    on_renew: Callable[[], None] | None = None,
) -> bool:
    claimed = claim(sessions, clock, lease_seconds, max_attempts)
    if claimed is None:
        return False
    run(sessions, clock, claimed, lease_seconds, max_attempts, reader, on_renew)
    return True


def reap(sessions: sessionmaker[Any], clock: Clock, max_attempts: int = 5) -> int:
    """Return running jobs whose lease has run out. Returns how many were reaped."""
    now = clock.now()
    with sessions.begin() as session:
        jobs = list(
            session.scalars(
                select(Job)
                .where(Job.state == "running", Job.lease_until < now)
                .with_for_update(skip_locked=True)
            )
        )
        for job in jobs:
            if job.attempts >= max_attempts:
                _fail(session, job, EXHAUSTED)
            else:
                job.state = "queued"
                job.lease_until = None
                job.run_after = None
        return len(jobs)
