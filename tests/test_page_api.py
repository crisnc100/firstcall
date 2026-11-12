"""API additions the pages need: a pool lead's business-minute wait with its team's
escalation minutes, and the escalations that fired."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board.escalate import escalate
from firstcall.db import Team
from tests.board_helpers import Board
from tests.conftest import FakeClock

MONDAY_10AM = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    return Board(sessions, clock)


def test_a_pool_lead_carries_its_business_wait_and_team_thresholds(board: Board) -> None:
    board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("admin@larkspur.example", roles=["admin"])
    lead = board.lead("north")
    # Arrived Saturday 17:50 local; Sunday is closed; read on Monday 09:10.
    board.set_lead(lead, arrival_at=datetime(2026, 2, 28, 22, 50, tzinfo=UTC))
    board.clock.current = datetime(2026, 3, 2, 14, 10, tzinfo=UTC)
    with board.sessions.begin() as session:
        session.execute(
            update(Team).where(Team.slug == "north").values(escalation={"team_unclaimed": 10})
        )
    seller = board.signed_in("seller@larkspur.example")
    row = seller.get(f"/api/leads/{lead}").json()
    assert row["wait_business_minutes"] == 20
    assert row["thresholds"] == {"late": 10, "very_late": 60}

    unassigned = board.lead(None)
    board.set_lead(unassigned, arrival_at=board.clock.now() - timedelta(minutes=90))
    admin = board.signed_in("admin@larkspur.example")
    detail = admin.get(f"/api/leads/{unassigned}").json()
    assert detail["wait_business_minutes"] == 90
    assert detail["thresholds"] == {"late": 15, "very_late": 60}


def test_an_owned_lead_or_a_bad_team_config_has_no_wait(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    client = board.signed_in(seller.email)
    owned = board.lead("north")
    board.own(owned, seller)
    assert client.get(f"/api/leads/{owned}").json()["wait_business_minutes"] is None
    pooled = board.lead("north")
    with board.sessions.begin() as session:
        session.execute(update(Team).where(Team.slug == "north").values(escalation={"x": 1}))
    row = client.get(f"/api/leads/{pooled}").json()
    assert (row["wait_business_minutes"], row["thresholds"]) == (None, None)


def test_escalations_list_what_fired_for_monitors_and_admins_only(board: Board) -> None:
    board.user("lead@larkspur.example", teams={"north": "team_lead"})
    board.user("monitor@larkspur.example", roles=["monitor"])
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = board.lead("north")
    board.set_lead(lead, arrival_at=MONDAY_10AM, pool_since=MONDAY_10AM)
    board.clock.current = MONDAY_10AM + timedelta(minutes=15)
    escalate(board.sessions, board.clock)
    monitor = board.signed_in("monitor@larkspur.example")
    [fired] = monitor.get("/api/escalations").json()
    assert (fired["level"], fired["current"], fired["lead"]["id"]) == (
        "team_unclaimed",
        True,
        str(lead),
    )
    board.own(lead, seller)
    assert monitor.get("/api/escalations").json()[0]["current"] is False
    assert board.signed_in(seller.email).get("/api/escalations").status_code == 404
    assert board.signed_in("lead@larkspur.example").get("/api/escalations").status_code == 404
