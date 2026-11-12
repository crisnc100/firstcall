"""The Not-a-lead tray and the monitor's rescue, and the alert routes a page needs: the
in-app inbox and push subscriptions."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import Alert, InboxEmail, Message, PushSubscription
from firstcall.settings import Settings
from tests.board_helpers import Board
from tests.conftest import FakeClock


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    return Board(sessions, clock)


def _spam(
    board: Board, listing: str | None = "Cedar Point Lofts", name: str | None = "Ana Ruiz"
) -> Message:
    payload = {
        "email": f"{uuid.uuid4().hex[:8]}@buyers.example",
        "message": "Real person, honest",
        "website": "http://filled-by-a-bot.example",
    }
    if listing:
        payload["listing"] = listing
    if name:
        payload["name"] = name
    message = board.pipe.deliver_form("site-form", payload)
    assert message.status == "not_a_lead"
    return message


def test_only_monitors_and_admins_see_the_tray(board: Board) -> None:
    board.user("monitor@larkspur.example", roles=["monitor"])
    board.user("seller@larkspur.example", teams={"north": "salesperson"})
    message = _spam(board)
    monitor = board.signed_in("monitor@larkspur.example")
    seller = board.signed_in("seller@larkspur.example")
    rows = monitor.get("/api/tray").json()
    assert [row["id"] for row in rows] == [str(message.id)]
    assert rows[0]["category"] == "spam"
    detail = monitor.get(f"/api/tray/{message.id}").json()
    assert "Real person, honest" in detail["text"]
    assert seller.get("/api/tray").status_code == 404
    assert seller.get(f"/api/tray/{message.id}").status_code == 404
    assert seller.post(f"/api/tray/{message.id}/rescue").status_code == 404
    assert monitor.get(f"/api/tray/{uuid.uuid4()}").status_code == 404


def test_a_rescue_makes_a_lead_records_who_and_alerts_the_pool(board: Board) -> None:
    board.user("monitor@larkspur.example", roles=["monitor"])
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    message = _spam(board)
    monitor = board.signed_in("monitor@larkspur.example")
    answer = monitor.post(f"/api/tray/{message.id}/rescue")
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "lead_created"
    saved = board.message(message.id)
    assert (saved.status, saved.category) == ("lead_created", "lead")
    assert saved.reason == "rescued from the Not-a-lead tray"
    lead = board.get(saved.lead_id)
    assert board.pipe.team_slug(lead) == "north"
    [created] = board.pipe.events(lead.id)
    assert created.type == "lead.created"
    monitor_id = monitor.get("/api/session").json()["user"]["id"]
    assert created.payload["rescued_by"] == monitor_id
    with board.sessions() as session:
        told = {a.user_id for a in session.scalars(select(Alert).where(Alert.lead_id == lead.id))}
    assert told == {seller.id}
    assert monitor.post(f"/api/tray/{message.id}/rescue").status_code == 409
    assert monitor.get(f"/api/tray/{message.id}").status_code == 404


def test_a_rescue_uses_the_stored_model_answer_and_makes_no_call(board: Board) -> None:
    board.user("admin@larkspur.example", roles=["admin"])
    message = _spam(board, name=None)
    with board.sessions.begin() as session:
        session.get_one(Message, message.id).model_answer = {
            "category": "spam",
            "name": "Ana Ruiz",
            "phone": None,
            "email": None,
            "item": "Saltmarsh Terrace",
            "agent": None,
            "team": "south",
        }
    answer = board.signed_in("admin@larkspur.example").post(f"/api/tray/{message.id}/rescue")
    assert answer.status_code == 200, answer.text
    # The shipped methods take the name from the model when the rules found none.
    assert board.get(board.message(message.id).lead_id).name == "Ana Ruiz"


def test_a_message_that_no_longer_reads_cannot_be_rescued(board: Board) -> None:
    board.user("monitor@larkspur.example", roles=["monitor"])
    message = _spam(board)
    with board.sessions.begin() as session:
        session.get_one(Message, message.id).raw = b"{not json"
    monitor = board.signed_in("monitor@larkspur.example")
    assert monitor.post(f"/api/tray/{message.id}/rescue").status_code == 409
    assert monitor.post(f"/api/tray/{uuid.uuid4()}/rescue").status_code == 404
    assert board.message(message.id).status == "not_a_lead"


def test_the_inbox_shows_my_alert_email_and_marks_it_read(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    other = board.user("other@larkspur.example", teams={"north": "salesperson"})
    board.lead("north", name="Ana Ruiz")
    board.clock.advance(seconds=60)
    assert board.outbound().email_digests() == 2
    client = board.signed_in(seller.email)
    [mail] = client.get("/api/inbox").json()
    assert mail["subject"] == "New lead in North: Ana"
    assert "Ana Ruiz" in mail["text"]
    assert mail["read"] is False
    assert client.post(f"/api/inbox/{mail['id']}/read").status_code == 204
    assert client.get("/api/inbox").json()[0]["read"] is True
    with board.sessions() as session:
        theirs = session.scalars(select(InboxEmail).where(InboxEmail.user_id == other.id)).one()
    assert client.post(f"/api/inbox/{theirs.id}/read").status_code == 404


def test_a_device_subscribes_moves_between_users_and_unsubscribes(
    sessions: sessionmaker[Session],
) -> None:
    board = Board(
        sessions,
        FakeClock(),
        Settings(env="test", vapid_public_key="BPublicKey", push_allow_http=False),
    )
    first = board.user("first@larkspur.example", teams={"north": "salesperson"})
    second = board.user("second@larkspur.example", teams={"north": "salesperson"})
    keys = {"p256dh": "BKey", "auth": "secret16bytes"}
    endpoint = "https://push.example/send/abc"
    one, two = board.signed_in(first.email), board.signed_in(second.email)
    assert one.get("/api/push/key").json() == {"public_key": "BPublicKey"}
    plain = {"endpoint": "http://push.example/x", "keys": keys}
    assert one.post("/api/push/subscriptions", json=plain).status_code == 422
    assert (
        one.post("/api/push/subscriptions", json={"endpoint": endpoint, "keys": keys}).status_code
        == 201
    )
    again = {"endpoint": endpoint, "keys": {**keys, "auth": "rotated"}}
    assert one.post("/api/push/subscriptions", json=again).status_code == 201
    assert (
        two.post("/api/push/subscriptions", json={"endpoint": endpoint, "keys": keys}).status_code
        == 201
    )

    def owners() -> list[tuple[uuid.UUID, str]]:
        with board.sessions() as session:
            return [(s.user_id, s.auth) for s in session.scalars(select(PushSubscription))]

    assert owners() == [(second.id, "secret16bytes")]
    one.request("DELETE", "/api/push/subscriptions", json={"endpoint": endpoint})
    assert owners() == [(second.id, "secret16bytes")]
    two.request("DELETE", "/api/push/subscriptions", json={"endpoint": endpoint})
    assert owners() == []
