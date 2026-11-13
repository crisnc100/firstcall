"""Leaving a team cancels only the alerts about leads the user can no longer see. A
monitor or admin still sees every lead and is still the audience for admin escalations,
so their alerts stay (fix check d4b23d x1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.board.escalate import escalate
from firstcall.db import Alert, User
from tests.board_helpers import Board
from tests.conftest import FakeClock

MONDAY_10AM = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    return Board(sessions, clock)


def _pending(board: Board, user_id: object) -> dict[str, str]:
    with board.sessions() as session:
        rows = session.scalars(select(Alert).where(Alert.user_id == user_id))
        return {a.kind: a.email_state for a in rows}


def test_a_monitor_who_leaves_a_team_keeps_their_alerts(board: Board) -> None:
    monitor = board.user("mo@larkspur.example", roles=["monitor"], teams={"north": "team_lead"})
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = board.lead("north")
    board.set_lead(lead, arrival_at=MONDAY_10AM, pool_since=MONDAY_10AM)
    board.clock.current = MONDAY_10AM + timedelta(minutes=61)
    assert escalate(board.sessions, board.clock) >= 2
    before = _pending(board, monitor.id)
    assert before["escalation:team_still_unclaimed"] == "pending"
    assert set(before.values()) == {"pending"}

    with board.sessions.begin() as session:
        accounts.set_memberships(session, session.get_one(User, monitor.id), {}, board.clock)
        accounts.set_memberships(session, session.get_one(User, seller.id), {}, board.clock)

    assert _pending(board, monitor.id) == before
    assert set(_pending(board, seller.id).values()) == {"refused"}
