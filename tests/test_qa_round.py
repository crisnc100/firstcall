"""The page QA round and the fix checks: urgency per pool stay, the team view, plain
addresses, digest retries, the user-row locks, and a claim under a bad team config."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts, leads
from firstcall.db import Alert, Lead, Team, User, UserSession
from firstcall.outbound import allowlist
from firstcall.settings import Settings
from tests.board_helpers import PASSWORD, Board
from tests.conftest import FakeClock

MONDAY_10AM = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
NEW_PASSWORD = "another long password"  # pragma: allowlist secret


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    return Board(sessions, clock)


def test_urgency_counts_the_current_pool_stay_not_the_arrival(board: Board) -> None:
    board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("admin@larkspur.example", roles=["admin"])
    board.clock.current = MONDAY_10AM + timedelta(minutes=90)
    placed = board.lead("north")
    board.set_lead(
        placed, arrival_at=MONDAY_10AM, pool_since=board.clock.now() - timedelta(minutes=3)
    )
    row = board.signed_in("seller@larkspur.example").get(f"/api/leads/{placed}").json()
    assert row["wait_business_minutes"] == 3

    unassigned = board.lead(None)
    board.set_lead(
        unassigned, arrival_at=MONDAY_10AM, pool_since=board.clock.now() - timedelta(minutes=4)
    )
    detail = board.signed_in("admin@larkspur.example").get(f"/api/leads/{unassigned}").json()
    assert detail["wait_business_minutes"] == 4


def test_the_wait_never_rounds_up_past_the_minute_the_timer_shows(board: Board) -> None:
    board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.clock.current = MONDAY_10AM + timedelta(minutes=59, seconds=59)
    lead = board.lead("north")
    board.set_lead(lead, arrival_at=MONDAY_10AM, pool_since=MONDAY_10AM)
    row = board.signed_in("seller@larkspur.example").get(f"/api/leads/{lead}").json()
    assert row["wait_business_minutes"] < 60


def test_the_team_view_lists_owned_and_nurture_leads_only(board: Board) -> None:
    board.user("lead@larkspur.example", teams={"north": "team_lead"})
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    waiting, owned, nurture = board.lead("north"), board.lead("north"), board.lead("north")
    board.own(owned, seller)
    board.own(nurture, seller)
    board.set_lead(nurture, state="nurture")
    rows = board.signed_in("lead@larkspur.example").get(
        f"/api/leads?view=team&team_id={board.teams['north']}"
    )
    assert {row["id"] for row in rows.json()} == {str(owned), str(nurture)}
    assert str(waiting) not in {row["id"] for row in rows.json()}


@pytest.mark.parametrize(
    "address",
    [
        "=?utf-8?q?buyer?=@larkspur.example",
        "=?utf-8?b?YnV5ZXI=?=@larkspur.example",
        "ana?x@larkspur.example",
        "ana..park@larkspur.example",
        ".ana@larkspur.example",
        "ana.@larkspur.example",
        "ana@-larkspur.example",
        "ana@larkspur-.example",
        "ana@larkspur.e1",
    ],
)
def test_encoded_words_and_odd_shapes_are_not_a_plain_address(board: Board, address: str) -> None:
    assert board.org_id is not None
    with board.sessions.begin() as session:
        with pytest.raises(accounts.AccountError, match="one plain address"):
            accounts.create_user(session, board.org_id, address, "Ana")
        with pytest.raises(allowlist.RefusedError, match="one plain address"):
            allowlist.check_email(session, board.org_id, address)


def test_ordinary_addresses_still_read(board: Board) -> None:
    for address in ("ana.park+leads@larkspur.example", "a_b-c%d@mail.larkspur.example"):
        assert board.user(address).email == address


class _DownMailer:
    def __init__(self) -> None:
        self.tries = 0

    def send(self, *args: Any) -> None:
        self.tries += 1
        raise OSError("mail server down")


def test_a_failing_digest_backs_off_and_gives_up_with_the_alerts_failed(board: Board) -> None:
    seller = board.user(
        f"s-{uuid.uuid4().hex[:6]}@larkspur.example", teams={"north": "salesperson"}
    )
    board.lead("north")
    board.clock.advance(seconds=60)
    mailer = _DownMailer()
    down = board.outbound(mailer=mailer)
    retries = Settings(env="test").email_retries

    def states() -> tuple[set[str], set[int]]:
        with board.sessions() as session:
            rows = session.scalars(select(Alert).where(Alert.user_id == seller.id)).all()
            return {a.email_state for a in rows}, {a.email_attempts for a in rows}

    assert down.email_digests() == 0
    assert states() == ({"pending"}, {1})
    assert down.email_digests() == 0
    assert mailer.tries == 1, "a failed digest must wait before it is tried again"

    for _ in range(retries + 2):
        board.clock.advance(seconds=3600)
        down.email_digests()
    assert mailer.tries == retries + 1
    assert states() == ({"failed"}, {retries + 1})


def test_a_digest_that_failed_once_goes_out_after_its_backoff(board: Board) -> None:
    seller = board.user(
        f"s-{uuid.uuid4().hex[:6]}@larkspur.example", teams={"north": "salesperson"}
    )
    board.lead("north")
    board.clock.advance(seconds=60)
    board.outbound(mailer=_DownMailer()).email_digests()
    assert board.outbound().email_digests() == 0
    board.clock.advance(seconds=3600)
    assert board.outbound().email_digests() == 1
    with board.sessions() as session:
        state = session.scalars(select(Alert.email_state).where(Alert.user_id == seller.id)).one()
    assert state == "sent"


def _second_connection_waits(board: Board, statement: str, **params: Any) -> bool:
    """Whether `statement`, run while the caller's transaction holds its lock, has to wait."""
    with board.sessions.begin() as other:
        other.execute(text("SET LOCAL lock_timeout = '300ms'"))
        try:
            other.execute(text(statement), params)
        except Exception as error:
            if "lock timeout" in str(error):
                return True
            raise
    return False


def test_locking_a_user_for_a_team_change_leaves_alert_inserts_free(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    with board.sessions.begin() as session:
        assert leads.lock_user(session, seller.id, share=False) is True
        key_share = "SELECT id FROM users WHERE id = :id FOR KEY SHARE"
        assert not _second_connection_waits(board, key_share, id=seller.id)
        share = "SELECT id FROM users WHERE id = :id FOR SHARE"
        assert _second_connection_waits(board, share, id=seller.id)


def test_set_password_locks_the_user_row_against_a_sign_in(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    with board.sessions.begin() as session:
        accounts.set_password(session, session.get_one(User, seller.id), NEW_PASSWORD)
        share = "SELECT id FROM users WHERE id = :id FOR SHARE"
        assert _second_connection_waits(board, share, id=seller.id)


def test_a_sign_in_checked_before_a_password_reset_starts_no_session(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    with board.sessions.begin() as signing_in:
        user = accounts.verify(signing_in, seller.email, PASSWORD)
        assert user is not None
        with board.sessions.begin() as admin:
            accounts.set_password(admin, admin.get_one(User, seller.id), NEW_PASSWORD)
        with pytest.raises(accounts.AccountError):
            accounts.start_session(signing_in, user, board.clock, 7)
    with board.sessions() as session:
        assert (
            session.scalars(select(UserSession).where(UserSession.user_id == seller.id)).all() == []
        )


def test_a_sign_in_waits_for_a_password_reset_in_flight(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    with board.sessions() as session:
        user = accounts.verify(session, seller.email, PASSWORD)
        assert user is not None
        session.expunge(user)
    outcome: dict[str, Any] = {}

    def sign_in() -> None:
        try:
            with board.sessions.begin() as session:
                outcome["started"] = accounts.start_session(
                    session, session.merge(user, load=False), board.clock, 7
                )
        except Exception as error:
            outcome["started"] = error

    with board.sessions.begin() as admin:
        accounts.set_password(admin, admin.get_one(User, seller.id), NEW_PASSWORD)
        thread = threading.Thread(target=sign_in)
        thread.start()
        time.sleep(0.5)
        assert thread.is_alive(), "the sign-in did not wait for the reset's lock"
    thread.join(10)
    assert isinstance(outcome["started"], accounts.AccountError)


def test_a_claim_under_a_bad_team_config_still_claims(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = board.lead("north")
    with board.sessions.begin() as session:
        session.execute(update(Team).where(Team.slug == "north").values(timezone="Mars/Olympus"))
    answer = board.signed_in(seller.email).post(f"/api/leads/{lead}/claim")
    assert answer.status_code == 200, answer.text
    with board.sessions() as session:
        claimed = session.get_one(Lead, lead)
    assert (claimed.state, claimed.owner_id) == ("owned", seller.id)
    assert claimed.claim_minutes is not None
    assert claimed.claim_business_minutes is None


def test_beta_needs_its_own_public_base_url() -> None:
    with pytest.raises(ValueError, match="FIRSTCALL_BASE_URL"):
        Settings(env="beta")
    assert Settings(env="beta", base_url="https://firstcall.example").base_url
