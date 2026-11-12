"""A rescue starts a new pool stay and is its own event in the lead's history, and the
lead page gets the names of the people its history mentions."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import Message
from tests.board_helpers import Board
from tests.conftest import FakeClock


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    return Board(sessions, clock)


def _spam(board: Board) -> Message:
    payload = {
        "name": "Sly Bot",
        "email": f"{uuid.uuid4().hex[:8]}@buyers.example",
        "listing": "Cedar Point Lofts",
        "website": "http://filled-by-a-bot.example",
    }
    message = board.pipe.deliver_form("site-form", payload)
    assert message.status == "not_a_lead"
    return message


def test_a_rescue_starts_a_new_pool_stay_and_claim_time_counts_from_arrival(
    board: Board,
) -> None:
    board.user("monitor@larkspur.example", roles=["monitor"], name="Mo Monitor")
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    message = _spam(board)
    received = board.message(message.id).received_at
    board.clock.current = received + timedelta(minutes=40)
    rescued_at = board.clock.now()
    monitor = board.signed_in("monitor@larkspur.example")
    assert monitor.post(f"/api/tray/{message.id}/rescue").status_code == 200
    lead_id = board.message(message.id).lead_id
    assert lead_id is not None
    lead = board.get(lead_id)
    assert lead.arrival_at == received
    assert lead.pool_since == rescued_at

    row = board.signed_in(seller.email).get(f"/api/leads/{lead_id}").json()
    assert row["wait_business_minutes"] == 0
    board.clock.advance(seconds=5 * 60)
    assert board.signed_in(seller.email).post(f"/api/leads/{lead_id}/claim").status_code == 200
    assert float(board.get(lead_id).claim_minutes or 0) == pytest.approx(45, abs=0.01)


def test_a_rescue_is_its_own_event_and_the_arrival_keeps_its_receipt_time(
    board: Board,
) -> None:
    monitor_user = board.user("monitor@larkspur.example", roles=["monitor"], name="Mo Monitor")
    message = _spam(board)
    received = board.message(message.id).received_at
    board.clock.current = received + timedelta(minutes=40)
    monitor = board.signed_in("monitor@larkspur.example")
    assert monitor.post(f"/api/tray/{message.id}/rescue").status_code == 200
    lead_id = board.message(message.id).lead_id

    detail = monitor.get(f"/api/leads/{lead_id}").json()
    created, rescued = detail["events"]
    assert created["type"] == "lead.created"
    assert abs(datetime.fromisoformat(created["at"]) - received) < timedelta(seconds=1)
    assert rescued["type"] == "lead.rescued"
    assert rescued["payload"]["by"] == str(monitor_user.id)
    assert detail["people"][str(monitor_user.id)] == "Mo Monitor"
