"""Push endpoints are read one way, by every parser on the path: odd characters are
refused, the host the check sees is the host the sender connects to, and the send goes to
the URL rebuilt from the checked parts. A send also holds its deadline against a slow
trickle, and a malformed endpoint is a 422 even with the local http escape hatch on."""

from __future__ import annotations

import socket
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import PushSend, PushSubscription
from firstcall.outbound import allowlist
from firstcall.outbound.push import WebPushSender, checked_endpoint
from firstcall.settings import Settings
from tests.board_helpers import Board, Device, vapid_keys
from tests.conftest import FakeClock

HOSTS = Settings(env="test").push_hosts
FCM = "https://fcm.googleapis.com/fcm/send/abc"

SMUGGLED = [
    # The shape that passed: urlsplit saw a notify.windows.com subdomain, urllib3 the
    # attacker's host, because only urllib3 ends a host at a backslash.
    "https://attacker.example\\.wns2.notify.windows.com/x",
    "https://fcm.googleapis.com\\@attacker.example/fcm/send/abc",
    "https://fcm.googleapis.com\\.attacker.example/x",
    "https://attacker.example\\@fcm.googleapis.com/x",
    "https://fcm.googleapis.com@attacker.example/x",
    "https://attacker.example@fcm.googleapis.com/x",
    "https://fcm.googleapis.com/x@attacker.example",
    "https://fcm.googleapis.com /x",
    "https://fcm.googleapis.com\t/x",
    "https://fcm.googleapis.com\n.attacker.example/x",
    "https://fcm.googleapis.com\x00.attacker.example/x",
    "https://fcm%2Egoogleapis.com/x",
    "https://fcm.googleapis.com%2F@attacker.example/x",
    "https://fcm.googleapis.c\N{GREEK SMALL LETTER OMICRON}m/x",
    "https://xn--fcm-googleapis-com.attacker.example/x",
    "https://fcm.googleapis.com\N{IDEOGRAPHIC FULL STOP}attacker.example/x",
    "https://fcm.googleapis.com./x",
    "https://[fcm.googleapis.com]/x",
    "https://fcm.googleapis.com:443:80/x",
]


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    return Board(sessions, clock)


@pytest.mark.parametrize("endpoint", SMUGGLED)
def test_a_smuggled_host_is_refused(endpoint: str) -> None:
    assert checked_endpoint(endpoint, HOSTS, allow_http=False) is None


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:9\\@x/y", "http://a b/", "http://[::1"])
def test_odd_shapes_are_refused_with_the_http_escape_hatch_too(endpoint: str) -> None:
    assert checked_endpoint(endpoint, HOSTS, allow_http=True) is None


def test_the_send_url_is_rebuilt_from_the_checked_parts() -> None:
    mixed = "HTTPS://FCM.GoogleAPIs.com:443/fcm/send/abc?x=1#frag"
    assert checked_endpoint(mixed, HOSTS, allow_http=False) == (
        "https://fcm.googleapis.com/fcm/send/abc?x=1"
    )
    assert checked_endpoint(FCM, HOSTS, allow_http=False) == FCM
    local = "http://127.0.0.1:8123/push/abc"
    assert checked_endpoint(local, HOSTS, allow_http=True) == local


@pytest.mark.parametrize("endpoint", SMUGGLED[:6])
def test_subscribe_refuses_a_smuggled_host(board: Board, endpoint: str) -> None:
    seller = board.user(
        f"s-{uuid.uuid4().hex[:6]}@larkspur.example", teams={"north": "salesperson"}
    )
    client = board.signed_in(seller.email)
    keys = Device(FCM).keys()
    answer = client.post("/api/push/subscriptions", json={"endpoint": endpoint, "keys": keys})
    assert answer.status_code == 422


def test_a_malformed_endpoint_is_a_422_with_the_http_escape_hatch(
    sessions: sessionmaker[Session],
) -> None:
    board = Board(sessions, FakeClock(), Settings(env="test", push_allow_http=True))
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    client = board.signed_in(seller.email)
    keys = Device(FCM).keys()
    for endpoint in ("http://[::1", "http://127.0.0.1:99999/x", "http://a\\@b/"):
        answer = client.post("/api/push/subscriptions", json={"endpoint": endpoint, "keys": keys})
        assert answer.status_code == 422, (endpoint, answer.status_code)


def _stored(board: Board, endpoint: str) -> PushSubscription:
    seller = board.user(
        f"s-{uuid.uuid4().hex[:6]}@larkspur.example", teams={"north": "salesperson"}
    )
    with board.sessions.begin() as session:
        row = PushSubscription(
            org_id=seller.org_id,
            user_id=seller.id,
            endpoint=endpoint,
            p256dh="AA",
            auth="AA",
            created_at=board.clock.now(),
        )
        session.add(row)
    return row


@pytest.mark.parametrize("endpoint", SMUGGLED[:6])
def test_a_stored_smuggled_endpoint_is_refused_at_send_time(board: Board, endpoint: str) -> None:
    row = _stored(board, endpoint)
    with board.sessions() as session:
        stored = session.get_one(PushSubscription, row.id)
        with pytest.raises(allowlist.RefusedError, match="push service"):
            allowlist.check_push(session, stored, stored.user_id, False, HOSTS)


class _Recording:
    def __init__(self) -> None:
        self.endpoints: list[str] = []

    def send(self, endpoint: str, p256dh: str, auth: str, payload: dict[str, Any]) -> int:
        self.endpoints.append(endpoint)
        return 201


def test_the_sender_gets_the_rebuilt_url_not_the_stored_string(board: Board) -> None:
    stored = "HTTPS://FCM.GoogleAPIs.com:443/fcm/send/abc#frag"
    _stored(board, stored)
    board.lead("north")
    outbound = board.outbound()
    outbound.settings = Settings(env="test")
    recorder = _Recording()
    outbound.pusher = recorder
    outbound.push_pending()
    assert recorder.endpoints == ["https://fcm.googleapis.com/fcm/send/abc"]
    with board.sessions() as session:
        assert {s.state for s in session.scalars(select(PushSend))} == {"sent"}


class _Trickle(BaseHTTPRequestHandler):
    """Answers, then sends one byte at a time, each just inside a socket read timeout."""

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(201)
        self.end_headers()
        try:
            for _ in range(1000):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.4)
        except OSError:
            return

    def log_message(self, format: str, *args: Any) -> None:
        return


class _SlowHeaders(BaseHTTPRequestHandler):
    """Never finishes its headers: one header byte at a time."""

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            self.wfile.write(b"HTTP/1.1 201 Created\r\n")
            for _ in range(1000):
                self.wfile.write(b"X")
                self.wfile.flush()
                time.sleep(0.4)
        except OSError:
            return

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture(params=[_Trickle, _SlowHeaders])
def slow_server(request: pytest.FixtureRequest) -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), request.param)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield int(server.server_address[1])
    server.shutdown()


def test_a_send_holds_its_total_deadline_against_a_trickle(slow_server: int) -> None:
    device = Device(f"http://127.0.0.1:{slow_server}/push/slow")
    private, _ = vapid_keys()
    sender = WebPushSender(private, "mailto:a@b.example", timeout=2.0)
    started = time.monotonic()
    sender.send(device.endpoint, device.p256dh, device.keys()["auth"], {"x": 1})
    assert time.monotonic() - started < 2.5


def test_nothing_listens_is_no_response_quickly() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    device = Device(f"http://127.0.0.1:{port}/push/none")
    private, _ = vapid_keys()
    sender = WebPushSender(private, "mailto:a@b.example", timeout=2.0)
    assert sender.send(device.endpoint, device.p256dh, device.keys()["auth"], {"x": 1}) == 0
