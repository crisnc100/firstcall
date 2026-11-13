"""The PR2 review fixes: push endpoints only on known push services, bounded push sends,
no alert to someone who lost the lead, in-app links only from server paths, and the
owner's own claim time."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.db import Alert, Lead, Membership, PushSend, PushSubscription, User
from firstcall.outbound import allowlist
from firstcall.outbound.push import WebPushSender, endpoint_allowed
from firstcall.settings import Settings
from tests.board_helpers import Board, Device, FakePushService, vapid_keys
from tests.conftest import FakeClock

HOSTS = Settings(env="test").push_hosts


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    return Board(sessions, clock)


@pytest.fixture
def push() -> Iterator[FakePushService]:
    service = FakePushService()
    yield service
    service.close()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://fcm.googleapis.com/fcm/send/abc",
        "https://updates.push.services.mozilla.com/wpush/v2/abc",
        "https://web.push.apple.com/QGx",
        "https://wns2-par02p.notify.windows.com/w/?token=abc",
        "https://fcm.googleapis.com:443/fcm/send/abc",
    ],
)
def test_known_push_services_are_allowed(endpoint: str) -> None:
    assert endpoint_allowed(endpoint, HOSTS, allow_http=False)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://push.example/a",
        "https://127.0.0.1/push",
        "https://[::1]/push",
        "https://10.0.0.8/push",
        "https://fcm.googleapis.com:8443/fcm/send/abc",
        "http://fcm.googleapis.com/fcm/send/abc",
        "https://user@fcm.googleapis.com/fcm/send/abc",
        "https://fcm.googleapis.com.attacker.example/x",
        "https://notify.windows.com.attacker.example/x",
        "https://evilnotify.windows.com/x",
        "https:///nohost",
        "not a url",
    ],
)
def test_other_endpoints_are_refused(endpoint: str) -> None:
    assert not endpoint_allowed(endpoint, HOSTS, allow_http=False)


def test_subscribe_refuses_an_endpoint_off_the_push_service_list(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    client = board.signed_in(seller.email)
    keys = Device("https://fcm.googleapis.com/fcm/send/abc").keys()
    for endpoint in ("https://internal.example/hook", "https://127.0.0.1/push"):
        answer = client.post("/api/push/subscriptions", json={"endpoint": endpoint, "keys": keys})
        assert answer.status_code == 422, endpoint
    ok = client.post(
        "/api/push/subscriptions",
        json={"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": keys},
    )
    assert ok.status_code == 201


def test_a_stored_endpoint_off_the_list_is_refused_at_send_time(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    with board.sessions.begin() as session:
        subscription = PushSubscription(
            org_id=seller.org_id,
            user_id=seller.id,
            endpoint="https://internal.example/hook",
            p256dh="AA",
            auth="AA",
            created_at=board.clock.now(),
        )
        session.add(subscription)
        session.flush()
        with pytest.raises(allowlist.RefusedError, match="push service"):
            allowlist.check_push(session, subscription, seller.id, False, HOSTS)


class _Slow(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(201)
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        try:
            for _ in range(10_000):
                self.wfile.write(b"x" * 4096)
                self.wfile.flush()
                time.sleep(0.01)
        except OSError:
            return

    def log_message(self, format: str, *args: Any) -> None:
        return


def test_a_push_send_stops_at_its_deadline_and_byte_cap() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        device = Device(f"http://127.0.0.1:{server.server_address[1]}/push/slow")
        private, _ = vapid_keys()
        sender = WebPushSender(private, "mailto:a@b.example", timeout=1.0, max_bytes=8192)
        started = time.monotonic()
        status = sender.send(device.endpoint, device.p256dh, device.keys()["auth"], {"x": 1})
        assert time.monotonic() - started < 3
        assert status == 201
        assert sender.last_body_bytes <= 8192
    finally:
        server.shutdown()


def test_a_digest_leaves_out_a_lead_the_recipient_can_no_longer_see(board: Board) -> None:
    seller = board.user(
        f"s-{uuid.uuid4().hex[:6]}@larkspur.example", teams={"north": "salesperson"}
    )
    board.lead("north")
    with board.sessions.begin() as session:
        user = session.get_one(User, seller.id)
        # Moved out of North by hand, without set_memberships cancelling anything, to
        # prove the send-time check on its own.
        session.query(Membership).filter(Membership.user_id == seller.id).delete()
        assert user.active
    board.clock.advance(seconds=60)
    assert board.outbound().email_digests() == 0
    with board.sessions() as session:
        states = set(session.scalars(select(Alert.email_state).where(Alert.user_id == seller.id)))
    assert states == {"refused"}


def test_a_push_is_not_sent_to_someone_who_lost_the_lead(
    board: Board, push: FakePushService
) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    device = push.device()
    board.subscribe(seller, device)
    board.lead("north")
    with board.sessions.begin() as session:
        session.query(Membership).filter(Membership.user_id == seller.id).delete()
    private, _ = vapid_keys()
    board.outbound(private).push_pending()
    assert push.to(device) == []
    with board.sessions() as session:
        assert {s.state for s in session.scalars(select(PushSend))} == {"failed"}


def test_leaving_a_team_cancels_pending_alerts_for_its_leads(
    board: Board, push: FakePushService
) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.subscribe(seller, push.device())
    board.lead("north")
    with board.sessions.begin() as session:
        accounts.set_memberships(session, session.get_one(User, seller.id), {}, board.clock)
    with board.sessions() as session:
        alerts = session.scalars(select(Alert).where(Alert.user_id == seller.id)).all()
        sends = session.scalars(select(PushSend)).all()
    assert {a.email_state for a in alerts} == {"refused"}
    assert {s.state for s in sends} == {"failed"}


def test_the_inbox_offers_only_server_paths_as_links(board: Board) -> None:
    seller = board.user(
        f"s-{uuid.uuid4().hex[:6]}@larkspur.example", teams={"north": "salesperson"}
    )
    hostile = "https://testserver//attacker.example/signin"
    lead = board.lead("north", name=hostile)
    board.clock.advance(seconds=60)
    assert board.outbound().email_digests() == 1
    [mail] = board.signed_in(seller.email).get("/api/inbox").json()
    assert mail["links"] == [{"label": hostile, "path": f"/leads/{lead}"}]


def test_the_owner_s_claim_time_is_their_own(board: Board) -> None:
    first = board.user("first@larkspur.example", teams={"north": "salesperson"})
    second = board.user("second@larkspur.example", teams={"north": "salesperson"})
    lead = board.lead("north")
    one, two = board.signed_in(first.email), board.signed_in(second.email)
    assert one.post(f"/api/leads/{lead}/claim").status_code == 200
    first_claim = board.clock.now()
    assert one.get(f"/api/leads/{lead}").json()["owned_since"] == first_claim.isoformat()
    board.clock.advance(seconds=3600)
    assert one.post(f"/api/leads/{lead}/release").status_code == 200
    assert one.get(f"/api/leads/{lead}").json()["owned_since"] is None
    board.clock.advance(seconds=3600)
    assert two.post(f"/api/leads/{lead}/claim").status_code == 200
    row = two.get(f"/api/leads/{lead}").json()
    assert row["owned_since"] == board.clock.now().isoformat()
    with board.sessions() as session:
        assert session.get_one(Lead, lead).claimed_at == first_claim
