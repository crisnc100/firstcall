"""The fixes from the phases 5-6 fix check: a follow-up moved to a newer step is only closed
by a contact made after the move, and a paid recording that misses answers keeps its record
pass's summary before the run fails."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from firstcall.board.followups import run_nightly
from firstcall.db import Followup
from firstcall.evaluation.note_eval import (
    IncompleteRecordingError,
    NoteRun,
    record_and_report,
    write_record_pass,
)
from firstcall.model.transport import TransportError, TransportRequest
from tests.board_helpers import Board
from tests.model_fakes import MODEL, PRICES, FakeTransport
from tests.test_note_reader import _owned, _reader, _suggestions, _work, _write, note_answer
from tests.test_note_reader_fixes import board as board  # a fixture, found by name


def _followups(board: Board, lead: Any) -> list[tuple[str, str]]:
    with board.sessions() as session:
        rows = session.execute(
            select(Followup.step, Followup.state).where(Followup.lead_id == lead)
        ).all()
    return [(step, state) for step, state in rows]


def test_a_contact_from_before_a_step_moved_does_not_close_the_newer_step(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    # Tuesday: the day-1 follow-up opens.
    board.clock.current = datetime(2026, 3, 3, 11, 0, tzinfo=UTC)
    run_nightly(board.sessions, board.clock)
    assert _followups(board, lead) == [("day 1", "open")]
    # Wednesday: a note mentions a call; its suggestion waits.
    board.clock.current = datetime(2026, 3, 4, 15, 0, tzinfo=UTC)
    note_id = _write(board, seller.email, lead)
    _work(board, _reader(board, FakeTransport(default=note_answer())))
    # Thursday: the same follow-up moves on to the day-3 step.
    board.clock.current = datetime(2026, 3, 5, 11, 0, tzinfo=UTC)
    run_nightly(board.sessions, board.clock)
    assert _followups(board, lead) == [("day 3", "open")]
    # Wednesday's call, confirmed now, keeps Wednesday's time and meets only the day-1 step.
    [suggestion] = _suggestions(board, note_id)
    client = board.signed_in(seller.email)
    assert client.post(f"/api/leads/{lead}/suggestions/{suggestion.id}/confirm").status_code == 200
    assert _followups(board, lead) == [("day 3", "open")]
    client.post(f"/api/leads/{lead}/notes", json={"text": "Texted her.", "action": "text"})
    assert _followups(board, lead) == [("day 3", "done")]


def _failing_first(seed_number: int) -> FakeTransport:
    from tests.test_note_eval import _gold_answers

    answers = _gold_answers(seed_number)

    def respond(request: TransportRequest) -> str:
        start = request.user.index("<note>\n") + len("<note>\n")
        return answers[request.user[start : request.user.rindex("\n</note>")]]

    # Both attempts at the first script fail, so recording leaves it unanswered.
    return FakeTransport(script=[TransportError("timeout")] * 2, respond=respond)


def test_a_recording_that_misses_answers_keeps_its_summary_then_fails(
    database_url: str, tmp_path: Path
) -> None:
    run_args = NoteRun(mode="record", model=MODEL, budget_usd=Decimal("1.00"),
                       recordings=tmp_path, transport=_failing_first(7), prices=PRICES)  # fmt: skip
    kept: list[dict[str, Any]] = []
    with pytest.raises(IncompleteRecordingError) as raised:
        record_and_report(7, database_url, run_args, limit=3, on_recorded=kept.append)
    [summary] = kept
    assert (summary["calls"], summary["ok"], summary["failed"]) == (4, 2, 2)
    assert len(summary["unanswered"]) == 1
    assert Decimal(summary["ledger_usd"]) > Decimal(summary["cost_usd"]) > 0
    assert raised.value.record_pass == summary
    message = str(raised.value)
    assert summary["unanswered"][0] in message
    assert f"${summary['ledger_usd']}" in message


def test_the_record_pass_summary_is_written_on_its_own(tmp_path: Path) -> None:
    summary = {"calls": 4, "ok": 2, "failed": 2, "cost_usd": "0.01", "ledger_usd": "0.02",
               "unanswered": ["s1"]}  # fmt: skip
    path = write_record_pass(summary, 7, "2026-11-13", tmp_path)
    assert path == tmp_path / "2026-11-13-notes-seed-7-record-pass.json"
    assert json.loads(path.read_text()) == summary


def test_the_cli_keeps_the_record_pass_then_fails_with_it_printed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from typer.testing import CliRunner

    from firstcall import cli
    from firstcall.evaluation import note_eval

    summary = {"calls": 4, "ok": 2, "failed": 2, "cost_usd": "0.010000",
               "ledger_usd": "0.020000", "unanswered": ["s1"]}  # fmt: skip
    order: list[str] = []

    def keep(paid: dict[str, Any], seed: int, date: str) -> Path:
        order.append("written")
        return write_record_pass(paid, seed, date, tmp_path)

    def record(*args: Any, on_recorded: Any, **kwargs: Any) -> dict[str, Any]:
        on_recorded(summary)
        order.append("replayed")
        raise IncompleteRecordingError(summary, "a replay miss")

    monkeypatch.setattr(note_eval, "write_record_pass", keep)
    monkeypatch.setattr(note_eval, "record_and_report", record)
    monkeypatch.setenv("FIRSTCALL_MODEL_KEY", "test-key")
    result = CliRunner().invoke(
        cli.app, ["eval-notes", "--mode", "record", "--date", "2026-11-13", "--limit", "3"]
    )
    assert result.exit_code == 1
    assert order == ["written", "replayed"]
    assert (
        json.loads((tmp_path / "2026-11-13-notes-seed-7-record-pass.json").read_text()) == summary
    )
    assert "$0.020000 on the ledger" in result.output
    assert "unanswered: s1" in result.output
    assert not list(tmp_path.glob("*-notes-seed-7.json"))


def test_a_follow_up_moved_before_the_upgrade_ignores_older_contacts() -> None:
    from types import SimpleNamespace
    from typing import cast
    from uuid import uuid4

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session as OrmSession

    from firstcall.board.followups import _meets  # pyright: ignore[reportPrivateUsage]
    from firstcall.db import Lead
    from tests.conftest import create_database, drop_database

    name = f"firstcall_migrate_{uuid4().hex[:12]}"
    url = create_database(name)
    moved, first = uuid4(), uuid4()
    try:
        config = Config("alembic.ini")
        config.attributes["url"] = url
        command.upgrade(config, "0010")
        engine = create_engine(url)
        with engine.begin() as conn:
            # Only the follow-ups matter; the replica role skips the foreign keys.
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            for followup_id, due_on in ((moved, "2026-03-05"), (first, "2026-03-03")):
                conn.execute(
                    text("INSERT INTO followups (id, org_id, lead_id, owner_id, due_on, step, "
                         "next_step, state, created_at) VALUES (:i, :o, :l, :u, :d, 'day 1', "
                         "'Call.', 'open', '2026-03-03 10:00+00')"),
                    {"i": followup_id, "o": uuid4(), "l": uuid4(), "u": uuid4(), "d": due_on},
                )  # fmt: skip
        command.upgrade(config, "head")
        with OrmSession(engine) as session:
            found = session.get_one(Followup, moved)
            untouched = session.get_one(Followup, first)
            assert found.moved_at is not None
            assert untouched.moved_at is None
            no_team = cast(Lead, SimpleNamespace(team_id=None))
            older_note = datetime(2026, 3, 4, 15, 0, tzinfo=UTC)
            assert not _meets(session, no_team, found, older_note)
            assert _meets(session, no_team, found, datetime.now(UTC))
            assert _meets(session, no_team, untouched, older_note)
        engine.dispose()
    finally:
        drop_database(name)
