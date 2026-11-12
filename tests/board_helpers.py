"""Building blocks for board tests: users, signed-in clients, leads in a known state, a fake
push service and a Mailpit reader."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import http_ece
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from py_vapid import Vapid01
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.api import create_app
from firstcall.board import accounts
from firstcall.db import Lead, Message, PushSubscription, Team, User
from firstcall.outbound.dispatch import Outbound
from firstcall.outbound.mail import InboxMailer
from firstcall.outbound.push import WebPushSender
from firstcall.settings import Settings
from tests.conftest import FakeClock
from tests.helpers import Pipeline

PASSWORD = "correct horse battery"  # pragma: allowlist secret
LISTINGS = {"north": "Cedar Point Lofts", "south": "Saltmarsh Terrace", "east": "Beacon Square"}
MAILPIT_API = os.environ.get("FIRSTCALL_TEST_MAILPIT", "http://127.0.0.1:8025")


class FakeMonotonic:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def vapid_keys() -> tuple[str, str]:
    """A fresh VAPID key pair: the private key as pywebpush reads it, and the public key."""
    vapid = Vapid01()
    vapid.generate_keys()
    assert vapid.private_key is not None
    assert vapid.public_key is not None
    private = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    public = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return b64(private), b64(public)


@dataclass
class Device:
    """A browser's push subscription, with the keys to read what was sent to it."""

    endpoint: str
    key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )
    auth: bytes = field(default_factory=lambda: os.urandom(16))

    @property
    def p256dh(self) -> str:
        return b64(
            self.key.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
        )

    def keys(self) -> dict[str, str]:
        return {"p256dh": self.p256dh, "auth": b64(self.auth)}

    def read(self, body: bytes) -> dict[str, Any]:
        plain = http_ece.decrypt(body, private_key=self.key, auth_secret=self.auth)
        return json.loads(plain)


@dataclass
class Received:
    path: str
    headers: dict[str, str]
    body: bytes
    at: float


class FakePushService:
    """A push service on localhost. Each path answers 201 unless told otherwise."""

    def __init__(self, host: str = "127.0.0.1") -> None:
        self.received: list[Received] = []
        self.answers: dict[str, list[int]] = {}
        self.lock = threading.Lock()
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                with service.lock:
                    service.received.append(
                        Received(self.path, dict(self.headers), body, time.time())
                    )
                    queued = service.answers.get(self.path, [])
                    status = queued.pop(0) if queued else 201
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                return

        self.server = ThreadingHTTPServer((host, 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def device(self, host: str = "127.0.0.1") -> Device:
        return Device(f"http://{host}:{self.port}/push/{uuid.uuid4().hex}")

    def to(self, device: Device) -> list[Received]:
        path = device.endpoint.split(f":{self.port}", 1)[1]
        with self.lock:
            return [r for r in self.received if r.path == path]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class Mailpit:
    """Reads what reached the local Mailpit over its HTTP API."""

    def __init__(self, base: str = MAILPIT_API) -> None:
        self.http = httpx.Client(base_url=base, timeout=10)

    def to(self, address: str) -> list[dict[str, Any]]:
        found = self.http.get("/api/v1/search", params={"query": f'to:"{address}"'})
        found.raise_for_status()
        return list(found.json()["messages"])

    def message(self, message_id: str) -> dict[str, Any]:
        return dict(self.http.get(f"/api/v1/message/{message_id}").json())

    def raw(self, message_id: str) -> str:
        return self.http.get(f"/api/v1/message/{message_id}/raw").text


@dataclass
class Board:
    """The demo org with a known cast, an API client per user, and lead helpers."""

    sessions: sessionmaker[Session]
    clock: FakeClock
    settings: Settings = field(default_factory=lambda: Settings(env="test"))
    pipe: Pipeline = field(init=False)
    monotonic: FakeMonotonic = field(default_factory=FakeMonotonic)

    def __post_init__(self) -> None:
        self.pipe = Pipeline(self.sessions, self.clock)
        self.app = create_app(self.settings, self.sessions, self.clock, self.monotonic)
        with self.sessions() as session:
            self.org_id = session.scalars(select(Team.org_id)).first()
            self.teams = {t.slug: t.id for t in session.scalars(select(Team))}

    def user(
        self,
        email: str,
        roles: list[str] | None = None,
        teams: dict[str, str] | None = None,
        password: str | None = PASSWORD,
        name: str | None = None,
    ) -> User:
        assert self.org_id is not None
        with self.sessions.begin() as session:
            user = accounts.create_user(
                session, self.org_id, email, name or email.split("@")[0].title(), roles, password
            )
            wanted = {self.teams[slug]: role for slug, role in (teams or {}).items()}
            accounts.set_memberships(session, user, wanted, self.clock)
            return user

    def client(self, base_url: str = "https://testserver") -> TestClient:
        return TestClient(self.app, base_url=base_url)

    def signed_in(self, email: str, password: str = PASSWORD) -> TestClient:
        client = self.client()
        csrf = client.get("/api/csrf").json()["csrf"]
        answer = client.post(
            "/api/session",
            json={"email": email, "password": password},
            headers={"X-CSRF-Token": csrf},
        )
        assert answer.status_code == 200, answer.text
        client.headers["X-CSRF-Token"] = answer.json()["csrf"]
        return client

    def lead(self, team: str | None, name: str = "Ana Ruiz", **fields: Any) -> uuid.UUID:
        """A new pool lead from a site-form post, in `team` or Unassigned."""
        payload = {
            "name": name,
            "phone": fields.pop("phone", f"202-555-{uuid.uuid4().int % 10000:04d}"),
            "email": fields.pop("email", f"{uuid.uuid4().hex[:10]}@buyers.example"),
            "message": "Is it still available?",
        }
        if team is not None:
            payload["listing"] = LISTINGS[team]
        message = self.pipe.deliver_form("site-form", payload)
        assert message.lead_id is not None, message.reason
        return message.lead_id

    def set_lead(self, lead_id: uuid.UUID, **values: Any) -> None:
        with self.sessions.begin() as session:
            session.execute(update(Lead).where(Lead.id == lead_id).values(**values))

    def own(self, lead_id: uuid.UUID, user: User, at: datetime | None = None) -> None:
        self.set_lead(lead_id, state="owned", owner_id=user.id, pool_since=None, claimed_at=at)

    def get(self, lead_id: uuid.UUID) -> Lead:
        return self.pipe.lead(lead_id)

    def message(self, message_id: uuid.UUID) -> Message:
        return self.pipe.message(message_id)

    def subscribe(self, user: User, device: Device) -> None:
        with self.sessions.begin() as session:
            session.add(
                PushSubscription(
                    org_id=user.org_id,
                    user_id=user.id,
                    endpoint=device.endpoint,
                    p256dh=device.p256dh,
                    auth=b64(device.auth),
                    created_at=self.clock.now(),
                )
            )

    def outbound(self, private_key: str | None = None, mailer: Any = None) -> Outbound:
        settings = Settings(env="test", push_allow_http=True)
        return Outbound(
            self.sessions,
            self.clock,
            settings,
            mailer or InboxMailer(),
            WebPushSender(private_key, settings.vapid_subject) if private_key else None,
        )
