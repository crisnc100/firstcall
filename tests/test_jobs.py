"""Jobs with a fake clock: leases, the reaper, retries and retries exhausted (F5)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import Job, Lead, Message
from firstcall.pipeline import jobs
from firstcall.pipeline.worker import beat, healthy, tick, work
from firstcall.settings import Settings
from tests.conftest import FakeClock
from tests.helpers import Pipeline, email

BODY = "Hi, I'd love to see Juniper Mesa.\n\nThanks,\nAna\n(202) 555-0142"


@pytest.fixture
def pipe(sessions: sessionmaker[Session], clock: FakeClock) -> Pipeline:
    return Pipeline(sessions, clock)


def stored(pipe: Pipeline) -> Message:
    return pipe.message(pipe.store("inbox", email("Ana Ruiz <ana@inbox.example>", BODY)))


def job_of(pipe: Pipeline, message: Message) -> Job:
    with pipe.sessions() as session:
        return session.query(Job).filter(Job.message_id == message.id).one()


def test_a_claim_takes_a_lease_and_counts_the_attempt(pipe: Pipeline) -> None:
    message = stored(pipe)
    claimed = jobs.claim(pipe.sessions, pipe.clock, 60, 5)
    assert claimed is not None
    assert claimed.message_id == message.id
    job = job_of(pipe, message)
    assert (job.state, job.attempts) == ("running", 1)
    assert job.lease_until == pipe.clock.now() + timedelta(seconds=60)
    assert jobs.claim(pipe.sessions, pipe.clock, 60, 5) is None


def test_the_reaper_leaves_a_live_lease_and_returns_an_expired_one(pipe: Pipeline) -> None:
    message = stored(pipe)
    assert jobs.claim(pipe.sessions, pipe.clock, 60, 5) is not None
    pipe.clock.advance(seconds=59)
    assert jobs.reap(pipe.sessions, pipe.clock) == 0
    assert job_of(pipe, message).state == "running"
    pipe.clock.advance(seconds=2)
    assert jobs.reap(pipe.sessions, pipe.clock) == 1
    job = job_of(pipe, message)
    assert (job.state, job.lease_until) == ("queued", None)
    assert pipe.run() == 1
    assert pipe.message(message.id).status == "lead_created"
    assert job_of(pipe, message).attempts == 2
    assert pipe.count(Lead) == 1


def test_the_reaper_fails_a_job_that_used_its_last_attempt(pipe: Pipeline) -> None:
    message = stored(pipe)
    for _ in range(5):
        assert jobs.claim(pipe.sessions, pipe.clock, 60, 5) is not None
        pipe.clock.advance(seconds=61)
        assert jobs.reap(pipe.sessions, pipe.clock, 5) == 1
    assert job_of(pipe, message).state == "failed"
    final = pipe.message(message.id)
    assert (final.status, final.reason) == ("failed", "retries_exhausted")
    assert jobs.claim(pipe.sessions, pipe.clock, 60, 5) is None


def test_errors_retry_with_backoff_then_exhaust(
    pipe: Pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object) -> str:
        raise RuntimeError("read blew up")

    monkeypatch.setattr(jobs, "process", broken)
    message = stored(pipe)
    for attempt in range(1, 6):
        assert jobs.run_one(pipe.sessions, pipe.clock) is True
        job = job_of(pipe, message)
        assert job.attempts == attempt
        assert job.last_error == "RuntimeError: read blew up"
        if attempt < 5:
            assert job.state == "queued"
            assert jobs.run_one(pipe.sessions, pipe.clock) is False, "backoff not honoured"
            pipe.clock.advance(seconds=2**attempt)
    assert job_of(pipe, message).state == "failed"
    final = pipe.message(message.id)
    assert (final.status, final.reason) == ("failed", "retries_exhausted")
    assert pipe.count(Lead) == 0


def test_a_queued_job_out_of_attempts_fails_when_claimed(pipe: Pipeline) -> None:
    message = stored(pipe)
    with pipe.sessions.begin() as session:
        session.query(Job).update({"attempts": 5})
    assert jobs.claim(pipe.sessions, pipe.clock, 60, 5) is None
    assert pipe.message(message.id).reason == "retries_exhausted"


def test_the_lease_is_renewed_while_a_job_runs(pipe: Pipeline) -> None:
    import time

    message = stored(pipe)
    claimed = jobs.claim(pipe.sessions, pipe.clock, 60, 5)
    assert claimed is not None
    pipe.clock.advance(seconds=30)
    with jobs.keep_lease(pipe.sessions, pipe.clock, claimed, 3):
        time.sleep(1.3)
    assert job_of(pipe, message).lease_until == pipe.clock.now() + timedelta(seconds=3)


def test_tick_runs_the_reaper(pipe: Pipeline, database_url: str) -> None:
    stored(pipe)
    assert jobs.claim(pipe.sessions, pipe.clock, 60, 5) is not None
    pipe.clock.advance(seconds=61)
    assert tick(pipe.sessions, pipe.clock, Settings(database_url=database_url)) == {"reaped": 1}


def test_the_worker_drains_the_queue_and_beats(pipe: Pipeline, database_url: str) -> None:
    for n in range(3):
        pipe.store("inbox", email(f"B{n} Lee <b{n}@inbox.example>", f"Juniper Mesa please {n}"))
    assert healthy(pipe.sessions, "worker") is False
    ran = work(pipe.sessions, pipe.clock, Settings(database_url=database_url), until_idle=True)
    assert ran == 3
    assert healthy(pipe.sessions, "worker") is True
    beat(pipe.sessions, "scheduler")
    assert healthy(pipe.sessions, "scheduler") is True
    with pipe.sessions() as session:
        assert {m.status for m in session.query(Message)} == {"lead_created"}
