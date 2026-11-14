"""The nightly follow-up job (F33): once per team and local date, time zones and clock
changes, a missed night, one open follow-up per lead, touches, cancels, a failing lead, a
crash mid-run, and no model call."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import followups
from firstcall.board.followups import FAILPOINT_DURING_RUN, run_nightly
from firstcall.db import Followup, FollowupRun, ModelCall, Team, User
from firstcall.pipeline.worker import followup_tick
from tests.board_helpers import Board
from tests.conftest import FakeClock

ROOT = Path(__file__).resolve().parents[1]
# Monday 2 March 2026, 05:00 in New York (UTC-5 until 8 March).
MONDAY_5AM = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
ARRIVAL = datetime(2026, 3, 1, 15, 0, tzinfo=UTC)  # Sunday 10:00 New York


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    clock.current = MONDAY_5AM
    return Board(sessions, clock)


def _owned(
    board: Board, owner: User, arrival: datetime = ARRIVAL, team: str = "north"
) -> uuid.UUID:
    lead = board.lead(team)
    board.own(lead, owner)
    board.set_lead(lead, arrival_at=arrival, owned_since=arrival)
    return lead


def _run(board: Board, at: datetime) -> dict[str, int]:
    board.clock.current = at
    return run_nightly(board.sessions, board.clock)


def _open(board: Board, lead: uuid.UUID) -> list[Followup]:
    with board.sessions() as session:
        return list(
            session.scalars(
                select(Followup).where(Followup.lead_id == lead, Followup.state == "open")
            )
        )


def _runs(board: Board) -> list[tuple[str, date]]:
    with board.sessions() as session:
        rows = session.execute(
            select(Team.slug, FollowupRun.local_date).join(Team, Team.id == FollowupRun.team_id)
        ).all()
        return sorted((slug, day) for slug, day in rows)


def test_the_job_runs_from_05_00_team_time_and_once_a_day(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    assert _run(board, MONDAY_5AM - timedelta(minutes=1))["teams"] == 0
    assert _open(board, lead) == []
    done = _run(board, MONDAY_5AM)
    # North, South and East are on New York time; Central and West have not reached 05:00.
    assert done["teams"] == 3
    assert done["opened"] == 1
    [found] = _open(board, lead)
    # Arrived Sunday: day 0 is closed, so it moved to Monday, and day 1 is Monday too.
    assert (found.due_on, found.step, found.owner_id) == (date(2026, 3, 2), "day 1", seller.id)
    assert found.next_step == "No contact yet, 1 day since arrival. Budget not known yet."
    assert [e.type for e in board.pipe.events(lead)][-1] == "followup.created"
    assert _run(board, MONDAY_5AM + timedelta(minutes=30))["teams"] == 0
    assert _runs(board).count(("north", date(2026, 3, 2))) == 1


def test_a_later_run_moves_the_one_open_follow_up(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    _run(board, MONDAY_5AM)
    # Day 3 is Wednesday 4 March.
    _run(board, MONDAY_5AM + timedelta(days=2))
    [found] = _open(board, lead)
    assert (found.due_on, found.step) == (date(2026, 3, 4), "day 3")
    created = [e for e in board.pipe.events(lead) if e.type == "followup.created"]
    assert len(created) == 1


def test_a_missed_night_is_caught_up_once_by_the_next_tick(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    # Nothing ran Monday or Tuesday; Wednesday at 14:00 New York the tick runs today's job.
    done = _run(board, datetime(2026, 3, 4, 19, 0, tzinfo=UTC))
    assert done["teams"] == 5
    assert ("north", date(2026, 3, 4)) in _runs(board)
    assert ("north", date(2026, 3, 2)) not in _runs(board)
    [found] = _open(board, lead)
    assert found.due_on == date(2026, 3, 4)


def test_a_touch_closes_the_follow_up_on_time(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    _run(board, MONDAY_5AM)
    board.clock.current = datetime(2026, 3, 2, 16, 0, tzinfo=UTC)
    answer = board.signed_in(seller.email).post(
        f"/api/leads/{lead}/notes", json={"text": "Called.", "action": "call"}
    )
    assert answer.status_code == 201
    with board.sessions() as session:
        found = session.scalars(select(Followup).where(Followup.lead_id == lead)).one()
    assert (found.state, found.closed_at) == ("done", board.clock.now())
    assert found.closed_at is not None
    assert found.closed_at.astimezone(_zone()).date() <= found.due_on
    types = [e.type for e in board.pipe.events(lead)]
    assert types[-2:] == ["contact.logged", "followup.done"]
    # The touch met today's step, so the next run opens nothing new.
    assert _run(board, MONDAY_5AM + timedelta(days=1))["opened"] == 0


def _zone() -> Any:
    from zoneinfo import ZoneInfo

    return ZoneInfo("America/New_York")


def test_an_overdue_follow_up_is_marked_and_sorted_first(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    due = _owned(board, seller)
    _run(board, MONDAY_5AM)
    # Arrives after the morning's run, so it has no follow-up yet.
    quiet = _owned(board, seller, arrival=MONDAY_5AM + timedelta(hours=4))
    board.clock.current = datetime(2026, 3, 3, 15, 0, tzinfo=UTC)
    rows = board.signed_in(seller.email).get("/api/leads?view=mine").json()
    assert [r["id"] for r in rows] == [str(due), str(quiet)]
    assert rows[0]["followup"]["overdue"] is True
    assert rows[0]["followup"]["due_on"] == "2026-03-02"
    assert rows[1]["followup"] is None


@pytest.mark.parametrize("move", ["release", "outcome"])
def test_a_lead_leaving_its_owner_cancels_its_follow_up(board: Board, move: str) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    _run(board, MONDAY_5AM)
    board.clock.current = MONDAY_5AM + timedelta(hours=5)
    client = board.signed_in(seller.email)
    if move == "release":
        assert client.post(f"/api/leads/{lead}/release").status_code == 200
    else:
        body = {"outcome": "lost", "reason": "price"}
        assert client.post(f"/api/leads/{lead}/outcome", json=body).status_code == 200
    assert _open(board, lead) == []
    event = [e for e in board.pipe.events(lead) if e.type == "followup.cancelled"][-1]
    assert event.payload["to"] == ("pool" if move == "release" else "lost")


def test_a_reassign_hands_the_follow_up_to_the_new_owner(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    other = board.user("other@larkspur.example", teams={"north": "salesperson"})
    lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
    lead = _owned(board, seller)
    _run(board, MONDAY_5AM)
    board.clock.current = MONDAY_5AM + timedelta(hours=5)
    answer = board.signed_in(lead_user.email).post(
        f"/api/leads/{lead}/reassign", json={"user_id": str(other.id)}
    )
    assert answer.status_code == 200
    [found] = _open(board, lead)
    assert found.owner_id == other.id


def test_a_team_cadence_overrides_the_default(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    with board.sessions.begin() as session:
        session.execute(
            update(Team)
            .where(Team.slug == "north")
            .values(cadence={"steps": [0], "steady": "daily"})
        )
    lead = _owned(board, seller)
    _run(board, MONDAY_5AM)
    [found] = _open(board, lead)
    assert found.step == "daily"


def test_a_team_whose_cadence_does_not_read_is_skipped_alone(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    with board.sessions.begin() as session:
        session.execute(update(Team).where(Team.slug == "north").values(cadence={"steps": []}))
    _owned(board, seller)
    done = _run(board, MONDAY_5AM)
    assert done["errors"] == 1
    assert ("north", date(2026, 3, 2)) not in _runs(board)
    assert ("south", date(2026, 3, 2)) in _runs(board)


def test_a_lead_that_raises_is_skipped_and_counted(
    board: Board, monkeypatch: pytest.MonkeyPatch
) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    bad, good = _owned(board, seller), _owned(board, seller)
    real = followups.follow_lead

    def flaky(session: Session, lead: Any, *args: Any) -> bool:
        if lead.id == bad:
            raise RuntimeError("broken lead")
        return real(session, lead, *args)

    monkeypatch.setattr(followups, "follow_lead", flaky)
    done = _run(board, MONDAY_5AM)
    assert (done["opened"], done["errors"]) == (1, 1)
    assert _open(board, bad) == []
    assert len(_open(board, good)) == 1
    with board.sessions() as session:
        run = session.scalars(
            select(FollowupRun)
            .join(Team, Team.id == FollowupRun.team_id)
            .where(Team.slug == "north")
        ).one()
        assert (run.leads, run.opened, run.errors) == (2, 1, 1)


def test_the_tick_survives_a_broken_job(board: Board, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any) -> dict[str, int]:
        raise RuntimeError("job broke")

    monkeypatch.setattr(followups, "run_nightly", broken)
    assert followup_tick(board.sessions, board.clock) == {"followup_teams": 0}


def test_the_job_makes_no_model_call(board: Board, monkeypatch: pytest.MonkeyPatch) -> None:
    from firstcall.model.gateway import Gateway

    calls: list[Any] = []
    monkeypatch.setattr(Gateway, "call", lambda self, request: calls.append(request))
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    _owned(board, seller)
    board.clock.current = MONDAY_5AM
    assert followup_tick(board.sessions, board.clock)["followup_opened"] == 1
    assert calls == []
    with board.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ModelCall)) == 0


# --- time zones and clock changes --------------------------------------------------------


def test_spring_forward_runs_at_05_00_on_the_new_offset(board: Board) -> None:
    board.user("seller@larkspur.example", teams={"north": "salesperson"})
    # Sunday 8 March: New York moves to UTC-4 at 02:00, so 05:00 is 09:00 UTC.
    assert ("north", date(2026, 3, 8)) not in _runs_after(
        board, datetime(2026, 3, 8, 8, 59, tzinfo=UTC)
    )
    assert ("north", date(2026, 3, 8)) in _runs_after(board, datetime(2026, 3, 8, 9, 0, tzinfo=UTC))


def test_fall_back_runs_at_05_00_on_the_new_offset(board: Board) -> None:
    board.user("seller@larkspur.example", teams={"north": "salesperson"})
    # Sunday 1 November: New York moves back to UTC-5 at 02:00, so 05:00 is 10:00 UTC.
    assert ("north", date(2026, 11, 1)) not in _runs_after(
        board, datetime(2026, 11, 1, 9, 59, tzinfo=UTC)
    )
    assert ("north", date(2026, 11, 1)) in _runs_after(
        board, datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    )


def _runs_after(board: Board, at: datetime) -> list[tuple[str, date]]:
    _run(board, at)
    return _runs(board)


def test_steps_follow_the_local_date_across_a_clock_change(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    # Saturday 7 March 23:30 New York is already Sunday in UTC; day 1 is Sunday, closed,
    # so it falls on Monday 9 March, after the change.
    lead = _owned(board, seller, arrival=datetime(2026, 3, 8, 4, 30, tzinfo=UTC))
    _run(board, datetime(2026, 3, 9, 9, 0, tzinfo=UTC))
    [found] = _open(board, lead)
    assert (found.due_on, found.step) == (date(2026, 3, 9), "day 1")


def test_a_team_whose_local_date_is_ahead_of_utc(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    with board.sessions.begin() as session:
        session.execute(
            update(Team).where(Team.slug == "north").values(timezone="Pacific/Auckland")
        )
    # Monday 2 March 16:00 UTC is Tuesday 3 March 05:00 in Auckland (UTC+13).
    arrival = datetime(2026, 3, 1, 22, 0, tzinfo=UTC)  # Monday 11:00 Auckland
    lead = _owned(board, seller, arrival=arrival)
    _run(board, datetime(2026, 3, 2, 15, 59, tzinfo=UTC))
    assert ("north", date(2026, 3, 3)) not in _runs(board)
    _run(board, datetime(2026, 3, 2, 16, 0, tzinfo=UTC))
    assert ("north", date(2026, 3, 3)) in _runs(board)
    assert ("north", date(2026, 3, 2)) not in _runs(board)
    [found] = _open(board, lead)
    assert (found.due_on, found.step) == (date(2026, 3, 3), "day 1")


# --- a crash mid-run ---------------------------------------------------------------------


def _killed_run(database_url: str, at: datetime) -> subprocess.CompletedProcess[str]:
    script = textwrap.dedent(
        f"""
        from datetime import datetime
        from firstcall.board.followups import run_nightly
        from firstcall.db import make_engine, make_sessions
        from firstcall.settings import Settings

        class Fixed:
            def now(self):
                return datetime.fromisoformat({at.isoformat()!r})

        sessions = make_sessions(make_engine(Settings().database_url))
        print(run_nightly(sessions, Fixed()))
        """
    )
    env = {**os.environ, "FIRSTCALL_DATABASE_URL": database_url,
           "FIRSTCALL_FAILPOINT": FAILPOINT_DURING_RUN}  # fmt: skip
    return subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True,
        timeout=120,
    )  # fmt: skip


def test_a_crash_mid_run_leaves_nothing_and_the_next_tick_redoes_it(
    board: Board, database_url: str
) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    leads = [_owned(board, seller) for _ in range(3)]
    killed = _killed_run(database_url, MONDAY_5AM)
    assert killed.returncode == 137, killed.stdout + killed.stderr
    assert f"FIRSTCALL_FAILPOINT fired {FAILPOINT_DURING_RUN}" in killed.stderr
    # East ran first and has no leads, so its run committed; North's died with its first lead.
    assert ("north", date(2026, 3, 2)) not in _runs(board)
    assert all(_open(board, lead) == [] for lead in leads)
    done = _run(board, MONDAY_5AM)
    assert done["opened"] == 3
    assert all(len(_open(board, lead)) == 1 for lead in leads)
    assert _runs(board).count(("north", date(2026, 3, 2))) == 1


def test_an_unmoved_follow_up_keeps_its_due_date_and_names_the_first_gap(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller, arrival=datetime(2026, 3, 2, 15, 0, tzinfo=UTC))
    client = board.signed_in(seller.email)
    profile = {"budget_min": 400000, "authority": "decision_maker", "need": "garage"}
    assert client.put(f"/api/leads/{lead}/profile", json=profile).status_code == 200
    _run(board, MONDAY_5AM + timedelta(days=1))
    # Wednesday: day 1 (Tuesday) is still the latest step, so the follow-up stays put.
    _run(board, MONDAY_5AM + timedelta(days=2))
    [found] = _open(board, lead)
    assert (found.due_on, found.step) == (date(2026, 3, 3), "day 1")
    assert found.next_step == "No contact yet, 2 days since arrival. Timeline not known yet."
    client.put(f"/api/leads/{lead}/profile", json={"timeline_text": "spring"})
    _run(board, MONDAY_5AM + timedelta(days=3))
    [found] = _open(board, lead)
    assert found.step == "day 3"
    assert found.next_step == "No contact yet, 3 days since arrival."
