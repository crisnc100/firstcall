"""Outcomes, nurture, reopen, and a buyer writing again to a closed lead (F34)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import Alert, Lead, Outcome, User
from tests.board_helpers import Board
from tests.conftest import FakeClock

MONDAY_10AM = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
BUYER = {"name": "Ana Ruiz", "email": "ana@buyers.example"}


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    clock.current = MONDAY_10AM
    return Board(sessions, clock)


class Cast:
    def __init__(self, board: Board) -> None:
        self.seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
        self.mate = board.user("mate@larkspur.example", teams={"north": "salesperson"})
        self.lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
        self.admin = board.user("admin@larkspur.example", roles=["admin"])
        self.board = board

    def claimed(self, **buyer: str) -> uuid.UUID:
        lead = self.board.lead("north", **buyer)
        now = self.board.clock.now()
        self.board.set_lead(lead, arrival_at=now, pool_since=now)
        answer = self.board.signed_in(self.seller.email).post(f"/api/leads/{lead}/claim")
        assert answer.status_code == 200
        return lead

    def post(self, user: User, lead: uuid.UUID, path: str, body: Any = None) -> Any:
        return self.board.signed_in(user.email).post(f"/api/leads/{lead}/{path}", json=body)


@pytest.fixture
def cast(board: Board) -> Cast:
    return Cast(board)


def _outcomes(board: Board, lead: uuid.UUID) -> list[tuple[str, str | None]]:
    with board.sessions() as session:
        rows = session.scalars(select(Outcome).where(Outcome.lead_id == lead).order_by(Outcome.at))
        return [(o.outcome, o.reason) for o in rows]


def _write_again(board: Board, website: str = "") -> Any:
    payload = {**BUYER, "listing": "Cedar Point Lofts", "message": "Hi again"}
    if website:
        payload["website"] = website
    return board.pipe.deliver_form("site-form", payload)


def _new_alerts(board: Board, since: set[uuid.UUID]) -> set[tuple[str, uuid.UUID]]:
    with board.sessions() as session:
        rows = session.scalars(select(Alert).where(Alert.id.not_in(since)))
        return {(a.kind, a.user_id) for a in rows}


def _alert_ids(board: Board) -> set[uuid.UUID]:
    with board.sessions() as session:
        return set(session.scalars(select(Alert.id)))


@pytest.mark.parametrize(
    "body",
    [
        {"outcome": "lost"},
        {"outcome": "lost", "reason": "cheaper"},
        {"outcome": "lost", "reason": "other"},
        {"outcome": "lost", "reason": "other", "reason_text": "   "},
        {"outcome": "won", "reason": "price"},
        {"outcome": "nurture"},
        {"outcome": "gone"},
    ],
)
def test_a_bad_outcome_is_refused(board: Board, cast: Cast, body: dict[str, str]) -> None:
    lead = cast.claimed()
    assert cast.post(cast.seller, lead, "outcome", body).status_code == 422
    assert board.get(lead).state == "owned"
    assert _outcomes(board, lead) == []


def test_lost_records_its_reason_and_competitor(board: Board, cast: Cast) -> None:
    lead = cast.claimed()
    body = {"outcome": "lost", "reason": "bought_elsewhere", "competitor": "Brightside Homes"}
    answer = cast.post(cast.seller, lead, "outcome", body)
    assert answer.status_code == 200, answer.text
    assert board.get(lead).state == "lost"
    with board.sessions() as session:
        row = session.scalars(select(Outcome).where(Outcome.lead_id == lead)).one()
        assert (row.outcome, row.reason, row.competitor, row.by) == (
            "lost",
            "bought_elsewhere",
            "Brightside Homes",
            cast.seller.id,
        )
    event = board.pipe.events(lead)[-1]
    assert (event.type, event.payload["outcome"], event.payload["reason"]) == (
        "lead.outcome",
        "lost",
        "bought_elsewhere",
    )
    assert "Brightside Homes" not in str(event.payload)


def test_other_keeps_its_text(board: Board, cast: Cast) -> None:
    lead = cast.claimed()
    body = {"outcome": "lost", "reason": "other", "reason_text": "Moved abroad"}
    assert cast.post(cast.seller, lead, "outcome", body).status_code == 200
    detail = board.signed_in(cast.seller.email).get(f"/api/leads/{lead}").json()
    assert detail["outcomes"][0]["reason_text"] == "Moved abroad"


def test_a_team_lead_sets_an_outcome_and_a_teammate_cannot(board: Board, cast: Cast) -> None:
    lead = cast.claimed()
    assert cast.post(cast.mate, lead, "outcome", {"outcome": "won"}).status_code == 404
    assert cast.post(cast.lead_user, lead, "outcome", {"outcome": "won"}).status_code == 200
    assert board.get(lead).state == "won"


def test_nurture_stays_open_and_resumes(board: Board, cast: Cast) -> None:
    lead = cast.claimed()
    assert cast.post(cast.seller, lead, "nurture").status_code == 200
    assert board.get(lead).state == "nurture"
    mine = board.signed_in(cast.seller.email).get("/api/leads?view=mine").json()
    assert [row["id"] for row in mine] == [str(lead)]
    assert _outcomes(board, lead) == [("nurture", None)]
    assert cast.post(cast.seller, lead, "resume").status_code == 200
    assert board.get(lead).state == "owned"
    assert [e.type for e in board.pipe.events(lead)][-2:] == ["lead.outcome", "lead.reopened"]


def test_a_buyer_writing_to_a_nurture_lead_brings_it_back(board: Board, cast: Cast) -> None:
    lead = cast.claimed(**BUYER)
    cast.post(cast.seller, lead, "nurture")
    message = _write_again(board)
    assert message.lead_id == lead
    assert board.get(lead).state == "owned"
    event = board.pipe.events(lead)[-1]
    assert (event.type, event.payload["to"]) == ("lead.activity", "owned")


def test_a_won_buyer_writing_again_adds_activity_and_alerts_the_owner(
    board: Board, cast: Cast
) -> None:
    lead = cast.claimed(**BUYER)
    cast.post(cast.seller, lead, "outcome", {"outcome": "won"})
    before = _alert_ids(board)
    message = _write_again(board)
    assert message.status == "attached"
    assert board.get(lead).state == "won"
    assert [e.type for e in board.pipe.events(lead)][-1] == "lead.activity"
    assert _new_alerts(board, before) == {("activity", cast.seller.id)}


@pytest.mark.parametrize("gone", ["deactivated", "left_team"])
def test_a_won_lead_whose_owner_left_alerts_the_team_leads(
    board: Board, cast: Cast, gone: str
) -> None:
    lead = cast.claimed(**BUYER)
    cast.post(cast.seller, lead, "outcome", {"outcome": "won"})
    _owner_gone(board, cast.seller, gone)
    before = _alert_ids(board)
    _write_again(board)
    assert board.get(lead).state == "won"
    assert board.get(lead).owner_id == cast.seller.id
    assert _new_alerts(board, before) == {("activity", cast.lead_user.id)}


def _owner_gone(board: Board, user: User, how: str) -> None:
    with board.sessions.begin() as session:
        if how == "deactivated":
            session.get_one(User, user.id).active = False
        else:
            session.execute(text("DELETE FROM memberships WHERE user_id = :u"), {"u": str(user.id)})


@pytest.mark.parametrize("outcome", ["lost", "not_real"])
def test_a_closed_buyer_writing_again_reopens_to_the_owner(
    board: Board, cast: Cast, outcome: str
) -> None:
    lead = cast.claimed(**BUYER)
    body = {"outcome": outcome, **({"reason": "timing"} if outcome == "lost" else {})}
    assert cast.post(cast.seller, lead, "outcome", body).status_code == 200
    board.clock.advance(minutes=30)
    before = _alert_ids(board)
    message = _write_again(board)
    row = board.get(lead)
    assert (row.state, row.owner_id) == ("owned", cast.seller.id)
    assert row.uncontacted_since == board.clock.now()
    events = board.pipe.events(lead)
    assert [e.type for e in events][-2:] == ["lead.activity", "lead.reopened"]
    assert events[-1].cause == f"message:{message.id}:reopen"
    assert events[-1].payload == {
        "message_id": str(message.id),
        "reason": "buyer_wrote",
        "to": "owned",
    }
    assert _new_alerts(board, before) == {("activity", cast.seller.id)}
    assert _outcomes(board, lead) == [(outcome, "timing" if outcome == "lost" else None)]


@pytest.mark.parametrize("gone", ["deactivated", "left_team"])
def test_a_closed_lead_whose_owner_left_reopens_to_the_pool(
    board: Board, cast: Cast, gone: str
) -> None:
    lead = cast.claimed(**BUYER)
    cast.post(cast.seller, lead, "outcome", {"outcome": "lost", "reason": "timing"})
    _owner_gone(board, cast.seller, gone)
    before = _alert_ids(board)
    _write_again(board)
    row = board.get(lead)
    assert (row.state, row.owner_id, row.pool_since) == ("pool", None, board.clock.now())
    assert board.pipe.events(lead)[-1].payload["to"] == "pool"
    # The pool hears of it; the departed owner is no longer in it.
    pool = {("reopened", cast.mate.id), ("reopened", cast.lead_user.id)}
    assert _new_alerts(board, before) == pool


def test_a_non_lead_message_does_not_reopen(board: Board, cast: Cast) -> None:
    lead = cast.claimed(**BUYER)
    cast.post(cast.seller, lead, "outcome", {"outcome": "lost", "reason": "timing"})
    before = _alert_ids(board)
    message = _write_again(board, website="https://spam.example")
    assert message.category == "spam"
    assert message.status == "attached"
    assert board.get(lead).state == "lost"
    assert [e.type for e in board.pipe.events(lead)][-1] == "lead.activity"
    assert _new_alerts(board, before) == {("activity", cast.seller.id)}


def _email(sender: str, body: str, extra: str = "", message_id: str | None = None) -> bytes:
    lines = [
        f"From: Ana Ruiz <{sender}>",
        "To: leads@larkspur.example",
        "Subject: Cedar Point Lofts",
        "Date: Mon, 02 Mar 2026 09:00:00 -0500",
        f"Message-ID: {message_id or f'<{uuid.uuid4().hex}@mail.example>'}",
    ]
    if extra:
        lines.append(extra)
    return ("\n".join(lines) + "\n\n" + body + "\n").encode()


def test_an_auto_reply_does_not_reopen(board: Board, cast: Cast) -> None:
    lead = cast.claimed(**BUYER)
    cast.post(cast.seller, lead, "outcome", {"outcome": "lost", "reason": "timing"})
    raw = _email(BUYER["email"], "I am away until Monday.", "Auto-Submitted: auto-replied")
    message = board.pipe.deliver("inbox", raw)
    assert message.status == "attached"
    assert message.lead_id == lead
    assert board.get(lead).state == "lost"


def test_a_real_email_reopens_and_a_re_forward_of_it_does_nothing(board: Board, cast: Cast) -> None:
    lead = cast.claimed(**BUYER)
    first = board.pipe.deliver("inbox", _email(BUYER["email"], "Still keen on the loft."))
    assert first.lead_id == lead
    cast.post(cast.seller, lead, "outcome", {"outcome": "lost", "reason": "timing"})
    events_before = len(board.pipe.events(lead))
    again = board.pipe.deliver("inbox", _email(BUYER["email"], "Still keen on the loft."))
    assert again.status == "duplicate_content"
    assert board.get(lead).state == "lost"
    assert len(board.pipe.events(lead)) == events_before
    real = board.pipe.deliver("inbox", _email(BUYER["email"], "Can we look again Friday?"))
    assert real.status == "attached"
    assert board.get(lead).state == "owned"


def test_a_manual_reopen_by_a_team_lead_or_admin(board: Board, cast: Cast) -> None:
    lead = cast.claimed()
    cast.post(cast.seller, lead, "outcome", {"outcome": "won"})
    assert cast.post(cast.seller, lead, "reopen").status_code == 404
    assert cast.post(cast.lead_user, lead, "reopen").status_code == 200
    row = board.get(lead)
    assert (row.state, row.owner_id) == ("owned", cast.seller.id)
    event = board.pipe.events(lead)[-1]
    assert (event.type, event.payload) == (
        "lead.reopened",
        {"by": str(cast.lead_user.id), "to": "owned"},
    )
    cast.post(cast.seller, lead, "outcome", {"outcome": "not_real"})
    assert cast.post(cast.admin, lead, "reopen").status_code == 200
    assert cast.post(cast.admin, lead, "reopen").status_code == 409


def test_outcome_history_is_append_only(board: Board, cast: Cast) -> None:
    lead = cast.claimed()
    cast.post(cast.seller, lead, "outcome", {"outcome": "won"})
    cast.post(cast.lead_user, lead, "reopen")
    cast.post(cast.seller, lead, "outcome", {"outcome": "lost", "reason": "price"})
    assert _outcomes(board, lead) == [("won", None), ("lost", "price")]
    for statement in (
        "UPDATE outcomes SET outcome = 'won' WHERE lead_id = :lead",
        "DELETE FROM outcomes WHERE lead_id = :lead",
    ):
        with pytest.raises(DBAPIError, match="append-only"), board.sessions.begin() as session:
            session.execute(text(statement), {"lead": str(lead)})
    assert _outcomes(board, lead) == [("won", None), ("lost", "price")]
    with board.sessions.begin() as session:
        session.execute(update(Lead).where(Lead.id == lead).values(deleted_at=MONDAY_10AM))
        session.execute(text("DELETE FROM outcomes WHERE lead_id = :lead"), {"lead": str(lead)})
    assert _outcomes(board, lead) == []
