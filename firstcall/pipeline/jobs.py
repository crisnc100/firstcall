"""Jobs in Postgres: claim with a lease, process, retry, reap.

A worker claims a job with FOR UPDATE SKIP LOCKED and holds a lease that it renews while
working. The reaper returns jobs whose lease ran out, so a dead worker's job is picked up
again. After max_attempts tries the message ends `failed: retries_exhausted`.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.core.clock import Clock
from firstcall.db import Job, Message
from firstcall.pipeline.process import process

log = logging.getLogger("firstcall.jobs")
EXHAUSTED = "retries_exhausted"


@dataclass(frozen=True)
class Claimed:
    job_id: uuid.UUID
    message_id: uuid.UUID
    attempt: int


def _fail(session: Session, job: Job, reason: str) -> None:
    job.state = "failed"
    job.lease_until = None
    session.execute(
        update(Message)
        .where(Message.id == job.message_id, Message.status.in_(("received", "processing")))
        .values(status="failed", reason=reason)
    )


def claim(
    sessions: sessionmaker[Any], clock: Clock, lease_seconds: int, max_attempts: int
) -> Claimed | None:
    now = clock.now()
    with sessions.begin() as session:
        while True:
            job = session.scalar(
                select(Job)
                .join(Message, Message.id == Job.message_id)
                .where(Job.state == "queued", or_(Job.run_after.is_(None), Job.run_after <= now))
                .order_by(Message.received_at, Job.id)
                .limit(1)
                .with_for_update(of=Job, skip_locked=True)
            )
            if job is None:
                return None
            if job.attempts >= max_attempts:
                _fail(session, job, EXHAUSTED)
                continue
            job.state = "running"
            job.attempts += 1
            job.lease_until = now + timedelta(seconds=lease_seconds)
            return Claimed(job.id, job.message_id, job.attempts)


@contextmanager
def keep_lease(
    sessions: sessionmaker[Any], clock: Clock, claimed: Claimed, lease_seconds: int
) -> Iterator[None]:
    """Renew the lease every third of its length until the block ends."""
    stop = threading.Event()

    def renew() -> None:
        while not stop.wait(lease_seconds / 3):
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
    lease_seconds: int,
    max_attempts: int,
) -> str | None:
    """Process a claimed job. The message's terminal status and the job's completion commit
    together; on an error the job goes back in the queue or, out of attempts, fails."""
    try:
        with keep_lease(sessions, clock, claimed, lease_seconds), sessions.begin() as session:
            job = session.get_one(Job, claimed.job_id, with_for_update=True)
            status = process(session, claimed.message_id, clock)
            job.state = "done"
            job.lease_until = None
            return status
    except Exception as error:
        log.exception("job %s failed on attempt %s", claimed.job_id, claimed.attempt)
        with sessions.begin() as session:
            job = session.get_one(Job, claimed.job_id, with_for_update=True)
            job.last_error = f"{type(error).__name__}: {error}"[:2000]
            if job.attempts >= max_attempts:
                _fail(session, job, EXHAUSTED)
            else:
                job.state = "queued"
                job.lease_until = None
                job.run_after = clock.now() + timedelta(seconds=2**job.attempts)
        return None


def run_one(
    sessions: sessionmaker[Any], clock: Clock, lease_seconds: int = 60, max_attempts: int = 5
) -> bool:
    claimed = claim(sessions, clock, lease_seconds, max_attempts)
    if claimed is None:
        return False
    run(sessions, clock, claimed, lease_seconds, max_attempts)
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
