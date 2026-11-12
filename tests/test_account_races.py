"""A password change ends other sessions, and a claim or reassign cannot leave a lead with
an inactive owner or a non-member (review fixes)."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts, leads
from firstcall.db import User
from tests.board_helpers import Board
from tests.conftest import FakeClock

NEW_PASSWORD = "another long password"  # pragma: allowlist secret


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    return Board(sessions, clock)


def test_an_admin_setting_a_password_ends_that_users_sessions(board: Board) -> None:
    board.user("admin@larkspur.example", roles=["admin"])
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    browser = board.signed_in(seller.email)
    admin = board.signed_in("admin@larkspur.example")
    answer = admin.put(f"/api/users/{seller.id}/password", json={"password": NEW_PASSWORD})
    assert answer.status_code == 204
    assert browser.get("/api/session").status_code == 401
    assert admin.get("/api/session").status_code == 200


def test_changing_your_own_password_keeps_only_the_session_you_used(board: Board) -> None:
    me = board.user("admin@larkspur.example", roles=["admin"])
    laptop = board.signed_in(me.email)
    phone = board.signed_in(me.email)
    assert (
        laptop.put(f"/api/users/{me.id}/password", json={"password": NEW_PASSWORD}).status_code
        == 204
    )
    assert laptop.get("/api/session").status_code == 200
    assert phone.get("/api/session").status_code == 401


def _hold(
    board: Board, first: Callable[[Session], Any], second: Callable[[Session], Any]
) -> tuple[Any, Any]:
    """Run `first` and keep its transaction open while `second` starts in another thread;
    `second` must wait for the lock, and runs on once `first` commits."""
    outcome: dict[str, Any] = {}

    def run_second() -> None:
        try:
            with board.sessions.begin() as session:
                outcome["second"] = second(session)
        except Exception as error:
            outcome["second"] = error

    with board.sessions.begin() as session:
        outcome["first"] = first(session)
        thread = threading.Thread(target=run_second)
        thread.start()
        time.sleep(0.5)
        assert thread.is_alive(), "the second transaction did not wait for the lock"
    thread.join(10)
    return outcome["first"], outcome["second"]


def _viewer(board: Board, user_id: uuid.UUID) -> Any:
    with board.sessions() as session:
        return accounts.viewer(session, session.get_one(User, user_id))


def _deactivate(board: Board, user_id: uuid.UUID) -> Callable[[Session], Any]:
    return lambda session: accounts.deactivate(session, session.get_one(User, user_id), board.clock)


def test_a_claim_after_a_deactivation_starts_finds_the_user_gone(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = board.lead("north")
    viewer = _viewer(board, seller.id)
    _, claimed = _hold(
        board,
        _deactivate(board, seller.id),
        lambda session: leads.claim(session, viewer, lead, board.clock),
    )
    assert isinstance(claimed, leads.NotFoundError)
    assert (board.get(lead).state, board.get(lead).owner_id) == ("pool", None)


def test_a_deactivation_after_a_claim_starts_returns_the_claimed_lead(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = board.lead("north")
    viewer = _viewer(board, seller.id)
    _, returned = _hold(
        board,
        lambda session: leads.claim(session, viewer, lead, board.clock),
        _deactivate(board, seller.id),
    )
    assert [done.lead_id for done in returned] == [lead]
    assert (board.get(lead).state, board.get(lead).owner_id) == ("pool", None)


def test_a_claim_after_leaving_the_team_starts_is_refused(board: Board) -> None:
    seller = board.user(
        "seller@larkspur.example", teams={"north": "salesperson", "south": "salesperson"}
    )
    lead = board.lead("north")
    viewer = _viewer(board, seller.id)
    south_only = {board.teams["south"]: "salesperson"}
    _, claimed = _hold(
        board,
        lambda session: accounts.set_memberships(
            session, session.get_one(User, seller.id), south_only, board.clock
        ),
        lambda session: leads.claim(session, viewer, lead, board.clock),
    )
    assert isinstance(claimed, leads.NotFoundError)
    assert board.get(lead).owner_id is None


def test_a_reassign_to_someone_being_deactivated_is_refused(board: Board) -> None:
    lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
    owner = board.user("owner@larkspur.example", teams={"north": "salesperson"})
    target = board.user("target@larkspur.example", teams={"north": "salesperson"})
    lead = board.lead("north")
    board.own(lead, owner)
    viewer = _viewer(board, lead_user.id)
    _, moved = _hold(
        board,
        _deactivate(board, target.id),
        lambda session: leads.reassign(session, viewer, lead, target.id, board.clock),
    )
    assert isinstance(moved, leads.InvalidError)
    assert board.get(lead).owner_id == owner.id
