"""Escalation counts from the current pool stay, and one team's bad config stops no other
team's escalation (review 80c30c f1, f2)."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board.escalate import escalate
from firstcall.core.escalation import overrides_from
from firstcall.corpus.world import load_world
from firstcall.db import Alert, Team
from firstcall.seed import seed
from tests.board_helpers import Board
from tests.conftest import FakeClock

MONDAY_10AM = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    return Board(sessions, clock)


def _fired(board: Board, at: datetime) -> set[tuple[uuid.UUID, str]]:
    board.clock.current = at
    with board.sessions() as session:
        before = {(a.lead_id, a.kind, a.cause) for a in session.scalars(select(Alert))}
    escalate(board.sessions, board.clock)
    with board.sessions() as session:
        after = {(a.lead_id, a.kind, a.cause) for a in session.scalars(select(Alert))}
    return {(lead, kind) for lead, kind, _ in after - before}


def test_a_lead_back_in_the_pool_escalates_again_from_its_new_stay(board: Board) -> None:
    board.user("lead@larkspur.example", teams={"north": "team_lead"})
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = board.lead("north")
    board.set_lead(lead, arrival_at=MONDAY_10AM, pool_since=MONDAY_10AM)
    level = "escalation:team_unclaimed"
    assert _fired(board, MONDAY_10AM + timedelta(minutes=15)) == {(lead, level)}
    client = board.signed_in(seller.email)
    board.clock.current = MONDAY_10AM + timedelta(minutes=20)
    assert client.post(f"/api/leads/{lead}/claim").status_code == 200
    board.clock.current = MONDAY_10AM + timedelta(minutes=30)
    assert client.post(f"/api/leads/{lead}/release").status_code == 200
    assert _fired(board, MONDAY_10AM + timedelta(minutes=44)) == set()
    assert _fired(board, MONDAY_10AM + timedelta(minutes=45)) == {(lead, level)}
    assert _fired(board, MONDAY_10AM + timedelta(minutes=46)) == set()


def test_placement_starts_the_team_clock_while_time_to_claim_keeps_arrival(board: Board) -> None:
    board.user("lead@larkspur.example", teams={"north": "team_lead"})
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("monitor@larkspur.example", roles=["monitor"])
    lead = board.lead(None)
    board.set_lead(lead, arrival_at=MONDAY_10AM, pool_since=MONDAY_10AM)
    board.clock.current = MONDAY_10AM + timedelta(minutes=40)
    monitor = board.signed_in("monitor@larkspur.example")
    placed = monitor.post(f"/api/leads/{lead}/place", json={"team_id": str(board.teams["north"])})
    assert placed.status_code == 200
    assert _fired(board, MONDAY_10AM + timedelta(minutes=41)) == set()
    assert _fired(board, MONDAY_10AM + timedelta(minutes=55)) == {
        (lead, "escalation:team_unclaimed")
    }
    board.clock.current = MONDAY_10AM + timedelta(minutes=56)
    claimed = board.signed_in(seller.email).post(f"/api/leads/{lead}/claim").json()["lead"]
    assert claimed["claim_business_minutes"] == 56


def test_one_teams_bad_config_does_not_stop_the_others(board: Board) -> None:
    board.user("north-lead@larkspur.example", teams={"north": "team_lead"})
    leads = {slug: board.lead(slug) for slug in ("north", "south", "east")}
    for lead in leads.values():
        board.set_lead(lead, arrival_at=MONDAY_10AM, pool_since=MONDAY_10AM)
    with board.sessions.begin() as session:
        session.execute(update(Team).where(Team.slug == "south").values(escalation={"x": 5}))
        session.execute(update(Team).where(Team.slug == "east").values(timezone="Mars/Olympus"))
    fired = _fired(board, MONDAY_10AM + timedelta(minutes=15))
    assert fired == {(leads["north"], "escalation:team_unclaimed")}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"escalation": {"team_unclaimed": -1}}, "positive"),
        ({"escalation": {"team_unclaimed": True}}, "positive"),
        ({"escalation": {"soon": 5}}, "unknown escalation level"),
        ({"business_hours": {"days": ["mon"], "open": "18:00", "close": "09:00"}}, "close after"),
        ({"timezone": "Mars/Olympus"}, "Mars/Olympus"),
    ],
)
def test_seed_refuses_a_team_with_bad_hours_or_minutes(
    sessions: sessionmaker[Session], change: dict[str, object], message: str
) -> None:
    world = load_world()
    raw = copy.deepcopy(world.raw)
    raw["teams"][0].update(change)
    with pytest.raises(ValueError, match=message):
        seed(sessions, type(world)(raw))


def test_overrides_from_reads_good_minutes() -> None:
    assert overrides_from({"team_unclaimed": 5, "unassigned_still": 2.5}) == {
        "team_unclaimed": 5.0,
        "unassigned_still": 2.5,
    }
