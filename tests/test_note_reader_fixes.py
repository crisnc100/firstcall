"""The fixes from the phases 5-6 review: budget bounds merge as a pair, notes from before
the reader get read, an older contact never closes a newer follow-up, one note never stops
the reread pass or the scheduler, the eval keeps its paid pass's audit, a call is logged even
when its note is deleted, and a note job and a delete never deadlock."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board.followups import run_nightly
from firstcall.core.note_read import parse_note_answer
from firstcall.db import Followup, Job, LeadProfile, ModelCall, Note, NoteSuggestion
from firstcall.evaluation.note_eval import NoteRun, record_and_report
from firstcall.model.transport import TransportError, TransportRequest
from firstcall.notes.reader import NoteReader, reread_notes
from firstcall.pipeline import worker
from firstcall.pipeline.jobs import claim, run
from firstcall.pipeline.model_read import ModelReader
from firstcall.settings import Settings
from tests.board_helpers import Board
from tests.conftest import FakeClock, create_database, drop_database
from tests.model_fakes import MODEL, PRICES, FakeTransport, gateway
from tests.test_note_reader import (
    MONDAY_10AM,
    _note,
    _owned,
    _reader,
    _suggestions,
    _work,
    _write,
    note_answer,
)


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    clock.current = MONDAY_10AM
    return Board(sessions, clock)


def _profile(board: Board, lead: uuid.UUID) -> LeadProfile:
    with board.sessions() as session:
        return session.scalars(select(LeadProfile).where(LeadProfile.lead_id == lead)).one()


def test_a_newer_budget_bound_replaces_both_bounds(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    _write(board, seller.email, lead, "Budget is around $450k.")
    _work(board, _reader(board, FakeTransport(default=note_answer(actions=[]))))
    board.clock.advance(minutes=5)
    _write(board, seller.email, lead, "Wants to spend at least $500k.")
    newer = {"text": "at least $500k", "min": 500000, "max": None}
    _work(board, _reader(board, FakeTransport(default=note_answer(budget=newer, actions=[]))))
    profile = _profile(board, lead)
    assert (profile.budget_min, profile.budget_max) == (500000, None)
    assert profile.budget_text == "at least $500k"


def test_a_person_set_bound_keeps_the_model_off_both_bounds(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    client = board.signed_in(seller.email)
    assert client.put(f"/api/leads/{lead}/profile", json={"budget_max": 400000}).status_code == 200
    _write(board, seller.email, lead)
    _work(board, _reader(board, FakeTransport(default=note_answer(actions=[]))))
    profile = _profile(board, lead)
    assert (profile.budget_min, profile.budget_max) == (None, 400000)
    assert profile.budget_text == "around $450k"


def test_notes_written_before_the_reader_wait_for_the_reread_pass() -> None:
    name = f"firstcall_migrate_{uuid.uuid4().hex[:12]}"
    url = create_database(name)
    try:
        config = Config("alembic.ini")
        config.attributes["url"] = url
        command.upgrade(config, "0010")
        engine = create_engine(url)
        with engine.begin() as conn:
            # Only the note matters here; the replica role skips the foreign keys a full
            # lead would need.
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text("INSERT INTO notes (id, org_id, lead_id, author_id, text, created_at) "
                     "VALUES (:i, :o, :l, :u, 'Called her.', now())"),
                {"i": uuid.uuid4(), "o": uuid.uuid4(), "l": uuid.uuid4(), "u": uuid.uuid4()},
            )  # fmt: skip
        command.upgrade(config, "head")
        with engine.connect() as conn:
            state = conn.execute(text("SELECT read_state FROM notes")).scalar_one()
        engine.dispose()
        assert state == "needs_model"
    finally:
        drop_database(name)


def test_an_older_contact_does_not_close_a_newer_follow_up(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    note_id = _write(board, seller.email, lead)
    _work(board, _reader(board, FakeTransport(default=note_answer())))
    # Day 3: the job opens the day-3 follow-up; the day-0 note's call is confirmed after.
    board.clock.current = datetime(2026, 3, 5, 11, 0, tzinfo=UTC)
    run_nightly(board.sessions, board.clock)
    with board.sessions.begin() as session:
        session.execute(
            update(Followup).where(Followup.lead_id == lead).values(due_on=date(2026, 3, 5))
        )
    [suggestion] = _suggestions(board, note_id)
    client = board.signed_in(seller.email)
    assert client.post(f"/api/leads/{lead}/suggestions/{suggestion.id}/confirm").status_code == 200
    with board.sessions() as session:
        states = session.scalars(select(Followup.state).where(Followup.lead_id == lead)).all()
    assert states == ["open"]
    # A contact made now does close it.
    client.post(f"/api/leads/{lead}/notes", json={"text": "Texted her.", "action": "text"})
    with board.sessions() as session:
        states = session.scalars(select(Followup.state).where(Followup.lead_id == lead)).all()
    assert states == ["done"]


def test_one_failing_note_is_counted_and_the_pass_moves_on(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    huge = _write(board, seller.email, lead, "Budget is $100000000000000000000.")
    board.clock.advance(minutes=1)
    fine = _write(board, seller.email, lead)
    _work(board, None)

    def respond(request: TransportRequest) -> str:
        if "100000000000000000000" in request.user:
            raise RuntimeError("the answer could not be stored")
        return note_answer()

    reader = NoteReader(_reader(board, FakeTransport(respond=respond)).gateway)
    assert reread_notes(board.sessions, reader) == {"errors": 1, "read": 1}
    assert (_note(board, huge).read_state, _note(board, huge).read_reason) == ("failed", "error")
    assert _note(board, fine).read_state == "read"


def test_a_replay_miss_halts_the_pass_without_raising(board: Board, tmp_path: Path) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    note_id = _write(board, seller.email, lead)
    _work(board, None)
    replay = gateway(board.sessions, board.clock, FakeTransport(), mode="replay",
                     recordings=tmp_path)  # fmt: skip
    assert reread_notes(board.sessions, NoteReader(replay)) == {"halted": 1}
    assert _note(board, note_id).read_state == "needs_model"


def test_a_huge_figure_or_a_nul_never_reaches_the_database() -> None:
    huge = note_answer(budget={"text": "x", "min": 10**20, "max": None})
    assert parse_note_answer(huge, "Called Ana") is None
    nul = parse_note_answer(note_answer(need="a gar\x00age", actions=[]), "Called Ana")
    assert nul is not None
    assert nul.need == "a garage"


def test_the_scheduler_survives_a_failing_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    rounds: list[str] = []

    def failing_tick(*args: Any, **kwargs: Any) -> dict[str, int]:
        rounds.append("tick")
        raise RuntimeError("boom")

    def stop(*args: Any, **kwargs: Any) -> dict[str, int]:
        rounds.append("followups")
        if len(rounds) >= 4:
            raise KeyboardInterrupt
        return {}

    monkeypatch.setattr(worker, "tick", failing_tick)
    monkeypatch.setattr(worker, "followup_tick", stop)
    monkeypatch.setattr(worker, "beat", lambda *a, **k: None)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    with pytest.raises(KeyboardInterrupt):
        worker.schedule(None, FakeClock(), Settings(env="test"))  # type: ignore[arg-type]
    assert rounds == ["tick", "followups", "tick", "followups"]


def test_the_report_keeps_the_paid_pass_failures_and_ledger(
    database_url: str, tmp_path: Path
) -> None:
    from tests.test_note_eval import _gold_answers

    answers = _gold_answers(7)

    def respond(request: TransportRequest) -> str:
        start = request.user.index("<note>\n") + len("<note>\n")
        return answers[request.user[start : request.user.rindex("\n</note>")]]

    transport = FakeTransport(script=[TransportError("timeout")], respond=respond)
    run_args = NoteRun(mode="record", model=MODEL, budget_usd=Decimal("1.00"),
                       recordings=tmp_path, transport=transport, prices=PRICES)  # fmt: skip
    report = record_and_report(7, database_url, run_args, limit=3)
    assert report["mode"] == "replay"
    paid = report["record_pass"]
    assert (paid["calls"], paid["ok"], paid["failed"]) == (4, 3, 1)
    assert Decimal(paid["ledger_usd"]) > Decimal(paid["cost_usd"])
    assert report["calls"]["calls"] == 3


def test_a_call_is_logged_when_its_note_is_deleted_meanwhile(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("admin@larkspur.example", roles=["admin"])
    lead = _owned(board, seller)
    _write(board, seller.email, lead)
    _work(board, None)

    def delete_then_answer(request: TransportRequest) -> str:
        admin = board.signed_in("admin@larkspur.example")
        assert admin.delete(f"/api/leads/{lead}").status_code == 204
        return note_answer()

    reader = NoteReader(_reader(board, FakeTransport(respond=delete_then_answer)).gateway)
    assert reread_notes(board.sessions, reader) == {}
    with board.sessions() as session:
        calls = session.scalars(select(ModelCall)).all()
    assert [(c.note_id, c.ok, c.cost_usd > 0) for c in calls] == [(None, True, True)]


def test_a_note_job_and_a_delete_never_deadlock(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("admin@larkspur.example", roles=["admin"])
    lead = _owned(board, seller)
    note_id = _write(board, seller.email, lead)
    entered, release = threading.Event(), threading.Event()

    def slow(request: TransportRequest) -> str:
        entered.set()
        release.wait(10)
        return note_answer()

    reader = ModelReader(gateway(board.sessions, board.clock, FakeTransport(respond=slow)))
    claimed = claim(board.sessions, board.clock, 60, 5)
    assert claimed is not None
    assert claimed.note_id == note_id
    ran: list[str | None] = []
    runner = threading.Thread(
        target=lambda: ran.append(run(board.sessions, board.clock, claimed, 60, 5, reader))
    )
    runner.start()
    assert entered.wait(10)
    admin = board.signed_in("admin@larkspur.example")
    deleted: list[int] = []
    deleter = threading.Thread(target=lambda: deleted.append(admin.delete(f"/api/leads/{lead}")
                                                              .status_code))  # fmt: skip
    deleter.start()
    deleter.join(5)
    finished_first = not deleter.is_alive()
    release.set()
    deleter.join(10)
    runner.join(10)
    assert finished_first, "the delete waited on the running note job"
    assert deleted == [204]
    assert ran == ["skipped"]
    with board.sessions() as session:
        assert session.scalars(select(Job).where(Job.note_id == note_id)).all() == []
        assert session.scalars(select(Note)).all() == []
        assert session.scalars(select(NoteSuggestion)).all() == []


def test_a_note_job_that_fails_after_its_lead_is_deleted_ends_quietly(board: Board) -> None:
    from firstcall.pipeline.jobs import run_one

    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("admin@larkspur.example", roles=["admin"])
    lead = _owned(board, seller)
    note_id = _write(board, seller.email, lead)

    def delete_then_fail(request: TransportRequest) -> str:
        admin = board.signed_in("admin@larkspur.example")
        assert admin.delete(f"/api/leads/{lead}").status_code == 204
        raise RuntimeError("the provider broke")

    reader = ModelReader(gateway(board.sessions, board.clock,
                                 FakeTransport(respond=delete_then_fail)))  # fmt: skip
    assert run_one(board.sessions, board.clock, reader=reader) is True
    with board.sessions() as session:
        assert session.scalars(select(Job).where(Job.note_id == note_id)).all() == []
