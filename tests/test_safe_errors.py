"""A database error never carries a note, a transcript or a message into a job's log line or
jobs.last_error (review s2): Postgres puts the failing row in its error text, so only the
error's class and SQLSTATE are kept."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import Job
from firstcall.errors import log_failure, safe_error
from firstcall.pipeline import jobs, worker
from firstcall.pipeline.jobs import TRANSCRIBE, claim
from firstcall.settings import Settings
from firstcall.voice.job import run_transcription
from tests.board_helpers import Board
from tests.conftest import FakeClock
from tests.helpers import Pipeline, email
from tests.voice_helpers import FakeTranscriber, upload

SENTINEL = "Zebra-Sentinel-4417"
SELLER = "seller@larkspur.example"


def _failed_insert(engine: Engine) -> IntegrityError:
    with engine.connect() as conn:
        conn.execute(text("CREATE TEMP TABLE memo_probe (said text CHECK (length(said) < 5))"))
        try:
            conn.execute(text("INSERT INTO memo_probe VALUES (:said)"), {"said": SENTINEL})
        except IntegrityError as error:
            return error
    raise AssertionError("the insert should have failed")


def test_a_database_error_is_reduced_to_its_class_and_code(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    error = _failed_insert(engine)
    assert SENTINEL in str(error), "Postgres no longer echoes the row; this test proves less"
    assert safe_error(error) == "IntegrityError (SQLSTATE 23514)"
    # Raised while handling one: its traceback would print the database error's text.
    outer = RuntimeError("while handling it")
    outer.__context__ = error
    assert safe_error(outer) == "RuntimeError"
    caplog.set_level(logging.DEBUG)
    log_failure(logging.getLogger("firstcall.test"), outer, "job %s failed", "j1")
    log_failure(logging.getLogger("firstcall.test"), error, "job %s failed", "j2")
    assert SENTINEL not in caplog.text
    assert "job j2 failed: IntegrityError (SQLSTATE 23514)" in caplog.text
    plain = ValueError("the audio is over 180 seconds")
    assert safe_error(plain) == "ValueError: the audio is over 180 seconds"


def test_a_failed_transcript_save_keeps_the_memo_out_of_logs_and_last_error(
    sessions: sessionmaker[Session], clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    clock.current = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
    board = Board(sessions, clock)
    seller = board.user(SELLER, teams={"north": "salesperson"})
    lead = board.lead("north")
    board.own(lead, seller, clock.current)
    assert upload(board.signed_in(SELLER), lead).status_code == 201
    claimed = claim(sessions, clock, 60, 5, kinds=(TRANSCRIBE,), skip=())
    assert claimed is not None
    with sessions.begin() as session:
        session.execute(
            text(f"ALTER TABLE notes ADD CONSTRAINT no_probe CHECK (text NOT LIKE '%{SENTINEL}%')")
        )
    caplog.set_level(logging.DEBUG)
    try:
        transcriber = FakeTranscriber(text=f"Called Ana about {SENTINEL} on Friday.")
        assert run_transcription(sessions, clock, claimed, 60, 5, transcriber) is None
    finally:
        with sessions.begin() as session:
            session.execute(text("ALTER TABLE notes DROP CONSTRAINT no_probe"))
    with sessions() as session:
        job = session.get_one(Job, claimed.job_id)
    assert (job.state, job.last_error) == ("queued", "IntegrityError (SQLSTATE 23514)")
    assert "job" in caplog.text
    assert SENTINEL not in caplog.text


def test_a_message_jobs_database_error_keeps_the_message_out_of_logs(
    sessions: sessionmaker[Session],
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = Pipeline(sessions, clock)
    message = pipe.store("inbox", email("Ana Ruiz <ana@inbox.example>", "Juniper Mesa please"))

    def process(session: Session, message_id: uuid.UUID, *args: Any) -> str:
        # Postgres quotes the value it could not read in the error's own message.
        session.execute(text("SELECT CAST(:said AS integer)"), {"said": SENTINEL})
        return "unreachable"

    monkeypatch.setattr(jobs, "process", process)
    caplog.set_level(logging.DEBUG)
    assert jobs.run_one(sessions, clock) is True
    with sessions() as session:
        job = session.scalars(select(Job).where(Job.message_id == message)).one()
    assert job.last_error == "DataError (SQLSTATE 22P02)"
    assert SENTINEL not in caplog.text


def test_the_schedulers_expiry_and_tick_log_a_database_error_without_its_row(
    engine: Engine, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = _failed_insert(engine)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise error

    def stop(*args: Any, **kwargs: Any) -> dict[str, int]:
        raise KeyboardInterrupt

    monkeypatch.setattr(worker, "expire_waiting", fail)
    monkeypatch.setattr(worker, "tick", fail)
    monkeypatch.setattr(worker, "followup_tick", stop)
    monkeypatch.setattr(worker, "beat", lambda *args: None)
    caplog.set_level(logging.DEBUG)
    clock = FakeClock()
    # Every database call is replaced above, so no sessions are needed.
    no_sessions: Any = None
    assert worker.expire_tick(no_sessions, clock) == {"expired_memos": 0}
    with pytest.raises(KeyboardInterrupt):
        worker.schedule(no_sessions, clock, Settings(env="test"))
    assert "expiring waiting memos failed: IntegrityError (SQLSTATE 23514)" in caplog.text
    assert "the scheduler's tick failed: IntegrityError (SQLSTATE 23514)" in caplog.text
    assert SENTINEL not in caplog.text
