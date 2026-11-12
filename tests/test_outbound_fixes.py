"""Sends that fail stay inside the dispatcher, addresses are one plain address, and a tray
sender is not a buyer (reviews 102a93 and 6ab337)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.db import Alert, Message, PushSend, PushSubscription
from firstcall.outbound import allowlist
from firstcall.outbound.mail import SmtpMailer
from firstcall.pipeline.worker import alert_tick
from tests.board_helpers import Board, Device, FakePushService, vapid_keys
from tests.conftest import FakeClock


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    return Board(sessions, clock)


@pytest.fixture
def push() -> Iterator[FakePushService]:
    service = FakePushService()
    yield service
    service.close()


def test_subscribe_refuses_keys_the_push_library_cannot_use(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    client = board.signed_in(seller.email)
    real = Device("https://push.example/a").keys()
    bad = [
        {"p256dh": "BKey", "auth": real["auth"]},
        {"p256dh": real["p256dh"], "auth": "c2hvcnQ"},
        {"p256dh": "!!not base64!!", "auth": real["auth"]},
        {"p256dh": "B" + "A" * 86, "auth": real["auth"]},
    ]
    for keys in bad:
        answer = client.post(
            "/api/push/subscriptions", json={"endpoint": "https://push.example/a", "keys": keys}
        )
        assert answer.status_code == 422, keys
    ok = client.post(
        "/api/push/subscriptions", json={"endpoint": "https://push.example/a", "keys": real}
    )
    assert ok.status_code == 201


def test_bad_stored_keys_fail_that_device_and_the_others_still_get_their_push(
    board: Board, push: FakePushService
) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    good = push.device()
    board.subscribe(seller, good)
    with board.sessions.begin() as session:
        session.add(
            PushSubscription(
                org_id=seller.org_id,
                user_id=seller.id,
                endpoint=f"http://127.0.0.1:{push.port}/push/broken",
                p256dh="AA",
                auth="!!",
                created_at=board.clock.now(),
            )
        )
    private, _ = vapid_keys()
    outbound = board.outbound(private)
    board.lead("north")
    for _ in range(5):
        outbound.push_pending()
    with board.sessions() as session:
        sends = {
            s.subscription_id: (s.state, s.attempts) for s in session.scalars(select(PushSend))
        }
        by_endpoint = {
            sub.endpoint: sends[sub.id] for sub in session.scalars(select(PushSubscription))
        }
    assert by_endpoint[good.endpoint] == ("sent", 1)
    assert by_endpoint[f"http://127.0.0.1:{push.port}/push/broken"] == ("failed", 4)
    assert len(push.to(good)) == 1


class _Exploding:
    def send(self, *args: Any) -> int:
        raise RuntimeError("the push library broke")


def test_any_push_exception_is_a_failed_attempt_not_a_crash(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.subscribe(seller, Device("https://push.example/explodes"))
    outbound = board.outbound()
    outbound.pusher = _Exploding()
    board.lead("north")
    assert outbound.push_pending() == 1
    with board.sessions() as session:
        send = session.scalars(select(PushSend)).one()
    assert (send.state, send.attempts) == ("pending", 1)


def test_an_smtp_failure_keeps_the_alerts_pending_for_the_next_pass(board: Board) -> None:
    seller = board.user(
        f"s-{uuid.uuid4().hex[:6]}@larkspur.example", teams={"north": "salesperson"}
    )
    board.lead("north")
    board.clock.advance(seconds=60)
    down = board.outbound(mailer=SmtpMailer("127.0.0.1", 1, "Firstcall <a@b.example>", timeout=2))
    assert down.email_digests() == 0
    with board.sessions() as session:
        states = set(session.scalars(select(Alert.email_state).where(Alert.user_id == seller.id)))
    assert states == {"pending"}
    assert board.outbound().email_digests() == 1


def test_alert_tick_runs_every_step_when_one_fails(
    board: Board, monkeypatch: pytest.MonkeyPatch
) -> None:
    board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.lead("north")
    board.clock.advance(seconds=60)

    def broken(*args: Any) -> int:
        raise RuntimeError("escalation broke")

    import firstcall.board.escalate as escalate_module

    monkeypatch.setattr(escalate_module, "escalate", broken)
    done = alert_tick(board.sessions, board.clock, board.outbound())
    assert done == {"escalated": 0, "pushed": 0, "emailed": 1}


@pytest.mark.parametrize(
    "address",
    [
        "Ana <ana@larkspur.example>",
        "ana@larkspur.example, buyer@buyers.example",
        "ana@larkspur.example (buyer@buyers.example)",
        '"ana"@larkspur.example',
        "ana@larkspur",
        "ana@larkspur.example\r\nBcc: x@y.example",
        "",
    ],
)
def test_only_one_plain_address_is_a_user_email_or_a_recipient(board: Board, address: str) -> None:
    assert board.org_id is not None
    with board.sessions.begin() as session:
        with pytest.raises(accounts.AccountError, match="one plain address"):
            accounts.create_user(session, board.org_id, address, "Ana")
        with pytest.raises(allowlist.RefusedError, match="one plain address"):
            allowlist.check_email(session, board.org_id, address)


def test_a_user_email_is_stored_bare_and_lowercased(board: Board) -> None:
    user = board.user("  Ana.Park@Larkspur.Example ")
    assert user.email == "ana.park@larkspur.example"


def test_a_sender_in_the_tray_is_not_a_buyer(board: Board) -> None:
    sender = f"colleague-{uuid.uuid4().hex[:6]}@larkspur.example"
    stored = board.pipe.form("site-form", {"name": "Sam", "email": sender, "message": "Hi"})
    with board.sessions.begin() as session:
        message = session.get_one(Message, stored)
        message.status, message.reason = "not_a_lead", "vendor (no signal)"
        message.extracted = {"category": "vendor", "email": sender}
    user = board.user(sender)
    assert board.org_id is not None
    with board.sessions() as session:
        assert allowlist.check_email(session, board.org_id, sender).id == user.id
