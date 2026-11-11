"""Intake: raw first, delivery keys, per-source auth and abuse limits (F3, F6)."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from firstcall.api import create_app
from firstcall.db import Job, Message
from firstcall.intake import auth, store
from firstcall.seed import seed, set_secret
from firstcall.settings import Settings
from tests.conftest import FakeClock

PARTNER_SECRET = "partner-test-secret"  # pragma: allowlist secret


class FakeMonotonic:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


class Intake:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        clock: FakeClock,
        settings: Settings,
    ) -> None:
        self.sessions = sessions
        self.clock = clock
        self.monotonic = FakeMonotonic()
        seed(sessions)
        self.site_token = set_secret(sessions, "site-form")
        self.inbox_password = set_secret(sessions, "inbox")
        self.calls_password = set_secret(sessions, "calls")
        self.app = create_app(settings, sessions, clock, self.monotonic)
        self.client = TestClient(self.app)

    def basic(self, user: str, password: str) -> dict[str, str]:
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def email(self, raw: bytes, **headers: str) -> Any:
        return self.client.post(
            "/intake/inbox",
            content=raw,
            headers={
                "Content-Type": "message/rfc822",
                **self.basic("inbox", self.inbox_password),
                **headers,
            },
        )

    def form(self, payload: dict[str, Any], token: str | None = None) -> Any:
        return self.client.post(
            f"/intake/site-form/{token or self.site_token}",
            content=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )

    def partner(
        self,
        body: bytes,
        timestamp: int | None = None,
        secret: str = PARTNER_SECRET,
        extra: dict[str, str] | None = None,
    ) -> Any:
        sent = int(self.clock.now().timestamp()) if timestamp is None else timestamp
        headers = {
            "Content-Type": "application/json",
            auth.TIMESTAMP_HEADER: str(sent),
            auth.SIGNATURE_HEADER: "sha256=" + auth.sign(secret, sent, body),
            **(extra or {}),
        }
        return self.client.post("/intake/partner-form", content=body, headers=headers)

    def count(self, model: Any) -> int:
        with self.sessions() as session:
            return session.scalar(select(func.count()).select_from(model)) or 0


def settings_for(database_url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {"database_url": database_url, "env": "test", **overrides}
    return Settings(**values)


@pytest.fixture
def intake(
    sessions: sessionmaker[Session],
    clock: FakeClock,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Intake:
    monkeypatch.setenv("FIRSTCALL_PARTNER_FORM_SECRET", PARTNER_SECRET)
    return Intake(sessions, clock, settings_for(database_url, ip_rate_per_minute=1000))


def raw_email(message_id: str | None = "<a1@mail.example>", body: str = "Hi there") -> bytes:
    lines = ["From: Ana Ruiz <ana@inbox.example>", "Subject: Hello"]
    if message_id:
        lines.append(f"Message-ID: {message_id}")
    return ("\n".join(lines) + "\n\n" + body + "\n").encode()


FORM = {"name": "Ana Ruiz", "phone": "202-555-0142", "email": "ana@inbox.example"}


# F3: raw first


def test_the_message_and_its_job_are_committed_before_the_2xx(intake: Intake) -> None:
    response = intake.email(raw_email())
    assert response.status_code == 202
    assert response.json()["status"] == "received"
    with intake.sessions() as session:
        message = session.get(Message, response.json()["message_id"])
        assert message is not None
        assert message.status == "received"
        assert message.raw == raw_email()
        job = session.scalar(select(Job).where(Job.message_id == message.id))
        assert job is not None
        assert job.state == "queued"


def test_a_failed_commit_answers_5xx_and_leaves_neither_row(
    intake: Intake, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(session: Session, message: Message) -> None:
        raise OperationalError("INSERT INTO jobs", {}, Exception("database went away"))

    monkeypatch.setattr(store, "add_job", broken)
    response = intake.email(raw_email())
    assert response.status_code == 503
    assert intake.count(Message) == 0
    assert intake.count(Job) == 0


def test_the_same_message_id_is_a_duplicate_delivery(intake: Intake) -> None:
    first = intake.email(raw_email(body="first"))
    again = intake.email(raw_email(body="the same id, resent"))
    assert again.status_code == 200
    assert again.json() == {
        "status": "duplicate_delivery",
        "message_id": first.json()["message_id"],
    }
    assert intake.count(Message) == 1
    assert intake.count(Job) == 1


def test_an_email_without_message_id_is_keyed_by_its_raw_bytes(intake: Intake) -> None:
    first = intake.email(raw_email(message_id=None, body="no id"))
    same = intake.email(raw_email(message_id=None, body="no id"))
    other = intake.email(raw_email(message_id=None, body="different text"))
    assert first.status_code == 202
    assert same.json()["status"] == "duplicate_delivery"
    assert other.status_code == 202
    assert intake.count(Message) == 2


def test_the_same_form_body_within_ten_minutes_is_a_duplicate_delivery(intake: Intake) -> None:
    first = intake.form(FORM)
    again = intake.form(FORM)
    assert first.status_code == 202
    assert again.status_code == 200
    assert again.json()["message_id"] == first.json()["message_id"]
    assert intake.count(Message) == 1


def test_the_same_form_body_after_ten_minutes_is_a_new_message(intake: Intake) -> None:
    first = intake.form(FORM)
    with intake.sessions.begin() as session:
        session.execute(
            update(Message).values(
                received_at=func.clock_timestamp() - func.make_interval(0, 0, 0, 0, 0, 11)
            )
        )
    again = intake.form(FORM)
    assert again.status_code == 202
    assert again.json()["message_id"] != first.json()["message_id"]
    assert intake.count(Message) == 2


def test_a_provider_delivery_id_dedupes_and_distinguishes(intake: Intake) -> None:
    body = json.dumps({"contact": {"first": "Ana"}}).encode()
    first = intake.partner(body, extra={"X-Delivery-Id": "dlv-1"})
    retried = intake.partner(
        b'{"contact": {"first": "Ana", "retry": true}}', extra={"X-Delivery-Id": "dlv-1"}
    )
    other = intake.partner(body, extra={"X-Delivery-Id": "dlv-2"})
    assert first.status_code == 202
    assert retried.json() == {
        "status": "duplicate_delivery",
        "message_id": first.json()["message_id"],
    }
    assert other.status_code == 202
    assert intake.count(Message) == 2


# F6: auth


def test_token_source_refuses_a_missing_or_wrong_token(intake: Intake) -> None:
    missing = intake.client.post(
        "/intake/site-form", content=b"{}", headers={"Content-Type": "application/json"}
    )
    assert missing.status_code == 401
    assert intake.form(FORM, token="not-the-token").status_code == 401
    assert intake.form(FORM).status_code == 202
    assert intake.count(Message) == 1


def test_a_rotated_token_stops_the_old_one(intake: Intake) -> None:
    old = intake.site_token
    new = set_secret(intake.sessions, "site-form")
    assert intake.form({"n": 1}, token=old).status_code == 401
    assert intake.form({"n": 2}, token=new).status_code == 202


def test_basic_source_refuses_missing_wrong_or_malformed_credentials(intake: Intake) -> None:
    raw = raw_email()
    headers = {"Content-Type": "message/rfc822"}
    assert intake.client.post("/intake/inbox", content=raw, headers=headers).status_code == 401
    wrong_password = {**headers, **intake.basic("inbox", "guess")}
    assert (
        intake.client.post("/intake/inbox", content=raw, headers=wrong_password).status_code == 401
    )
    wrong_user = {**headers, **intake.basic("calls", intake.inbox_password)}
    assert intake.client.post("/intake/inbox", content=raw, headers=wrong_user).status_code == 401
    for value in (
        "Basic !!!not-base64",
        "Bearer abc",
        "Basic " + base64.b64encode(b"\xff").decode(),
    ):
        malformed = {**headers, "Authorization": value}
        assert (
            intake.client.post("/intake/inbox", content=raw, headers=malformed).status_code == 401
        )
    with_token = {**headers, **intake.basic("inbox", intake.inbox_password)}
    assert (
        intake.client.post("/intake/inbox/sometoken", content=raw, headers=with_token).status_code
        == 401
    )
    assert intake.count(Message) == 0
    assert intake.email(raw).status_code == 202


def test_hmac_source_refuses_missing_wrong_or_stale_signatures(intake: Intake) -> None:
    body = b'{"contact": {"first": "Ana"}}'
    now = int(intake.clock.now().timestamp())
    plain = {"Content-Type": "application/json"}
    assert (
        intake.client.post("/intake/partner-form", content=body, headers=plain).status_code == 401
    )
    wrong = "not-" + PARTNER_SECRET
    assert intake.partner(body, secret=wrong).status_code == 401
    assert intake.partner(body, timestamp=now - 301).status_code == 401
    assert intake.partner(body, timestamp=now + 301).status_code == 401
    bad_timestamp = {
        **plain,
        auth.TIMESTAMP_HEADER: "yesterday",
        auth.SIGNATURE_HEADER: auth.sign(PARTNER_SECRET, now, body),
    }
    assert (
        intake.client.post("/intake/partner-form", content=body, headers=bad_timestamp).status_code
        == 401
    )
    tampered = {
        **plain,
        auth.TIMESTAMP_HEADER: str(now),
        auth.SIGNATURE_HEADER: auth.sign(PARTNER_SECRET, now, body + b" "),
    }
    assert (
        intake.client.post("/intake/partner-form", content=body, headers=tampered).status_code
        == 401
    )
    signed = {
        **plain,
        auth.TIMESTAMP_HEADER: str(now),
        auth.SIGNATURE_HEADER: auth.sign(PARTNER_SECRET, now, body),
    }
    assert (
        intake.client.post("/intake/partner-form/tok", content=body, headers=signed).status_code
        == 401
    )
    assert intake.count(Message) == 0
    assert intake.partner(body, timestamp=now - 299).status_code == 202


def test_hmac_source_refuses_everything_when_its_secret_is_not_set(
    intake: Intake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FIRSTCALL_PARTNER_FORM_SECRET")
    assert intake.partner(b"{}").status_code == 401
    assert intake.partner(b"{}", secret="").status_code == 401


def test_an_unset_basic_password_or_token_refuses_everyone(
    sessions: sessionmaker[Session], clock: FakeClock, database_url: str
) -> None:
    seed(sessions)
    client = TestClient(create_app(settings_for(database_url), sessions, clock))
    email = {
        "Content-Type": "message/rfc822",
        "Authorization": "Basic " + base64.b64encode(b"inbox:").decode(),
    }
    assert client.post("/intake/inbox", content=raw_email(), headers=email).status_code == 401
    form = {"Content-Type": "application/json"}
    assert client.post("/intake/site-form/anything", content=b"{}", headers=form).status_code == 401


# F6: abuse


def test_an_unknown_source_is_404(intake: Intake) -> None:
    response = intake.client.post(
        "/intake/nope", content=b"{}", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 404


def test_the_wrong_content_type_is_415(intake: Intake) -> None:
    as_json = {"Content-Type": "application/json", **intake.basic("inbox", intake.inbox_password)}
    assert intake.client.post("/intake/inbox", content=b"{}", headers=as_json).status_code == 415
    as_text = {"Content-Type": "text/plain"}
    response = intake.client.post(
        f"/intake/site-form/{intake.site_token}", content=b"{}", headers=as_text
    )
    assert response.status_code == 415
    with_charset = {"Content-Type": "application/json; charset=utf-8"}
    response = intake.client.post(
        f"/intake/site-form/{intake.site_token}", content=b"{}", headers=with_charset
    )
    assert response.status_code == 202


def test_a_declared_oversize_body_is_413(intake: Intake) -> None:
    body = b"x" * 1_000_001
    response = intake.client.post(
        f"/intake/site-form/{intake.site_token}",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert intake.count(Message) == 0


def test_an_undeclared_oversize_body_is_refused_before_it_is_fully_read(intake: Intake) -> None:
    """A chunked body with no Content-Length: the app stops pulling chunks at the cap."""
    import asyncio

    chunk = b"x" * 65_536
    total_chunks = 40  # 2.6 MB against a 1 MB cap
    pulled = 0
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal pulled
        pulled += 1
        return {"type": "http.request", "body": chunk, "more_body": pulled < total_chunks}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"/intake/site-form/{intake.site_token}",
        "raw_path": f"/intake/site-form/{intake.site_token}".encode(),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"transfer-encoding", b"chunked")],
        "client": ("127.0.0.1", 5000),
        "server": ("testserver", 80),
    }
    app: Any = intake.app
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413
    assert pulled <= 1_000_000 // len(chunk) + 1
    assert pulled < total_chunks
    assert intake.count(Message) == 0


def test_an_email_may_be_larger_than_a_form(intake: Intake) -> None:
    raw = raw_email(body="y" * 2_000_000)
    assert intake.email(raw).status_code == 202
    assert intake.email(raw_email("<big@x>", body="y" * 10_000_001)).status_code == 413


def test_more_than_60_requests_a_minute_for_one_source_is_429(intake: Intake) -> None:
    for n in range(60):
        assert intake.form({"n": n}).status_code == 202
    assert intake.form({"n": 60}).status_code == 429
    intake.monotonic.value += 60
    assert intake.form({"n": 61}).status_code == 202


def test_more_than_30_requests_a_minute_from_one_address_is_429(
    sessions: sessionmaker[Session], clock: FakeClock, database_url: str
) -> None:
    seed(sessions)
    token = set_secret(sessions, "site-form")
    monotonic = FakeMonotonic()
    client = TestClient(create_app(settings_for(database_url), sessions, clock, monotonic))
    headers = {"Content-Type": "application/json"}
    for n in range(30):
        response = client.post(
            f"/intake/site-form/{token}", content=f'{{"n": {n}}}', headers=headers
        )
        assert response.status_code == 202
    assert (
        client.post(f"/intake/site-form/{token}", content=b"{}", headers=headers).status_code == 429
    )
    # Without a trusted proxy, a forged X-Forwarded-For changes nothing.
    forged = {**headers, "X-Forwarded-For": "203.0.113.9"}
    assert (
        client.post(f"/intake/site-form/{token}", content=b"{}", headers=forged).status_code == 429
    )


def test_x_forwarded_for_is_trusted_only_behind_a_proxy(
    sessions: sessionmaker[Session], clock: FakeClock, database_url: str
) -> None:
    seed(sessions)
    token = set_secret(sessions, "site-form")
    settings = settings_for(database_url, trust_proxy=True)
    client = TestClient(create_app(settings, sessions, clock, FakeMonotonic()))
    headers = {"Content-Type": "application/json", "X-Forwarded-For": "203.0.113.9, 10.0.0.1"}
    for n in range(30):
        assert (
            client.post(
                f"/intake/site-form/{token}", content=f'{{"n": {n}}}', headers=headers
            ).status_code
            == 202
        )
    assert (
        client.post(f"/intake/site-form/{token}", content=b"{}", headers=headers).status_code == 429
    )
    other = {**headers, "X-Forwarded-For": "198.51.100.7"}
    assert (
        client.post(f"/intake/site-form/{token}", content=b'{"n": 99}', headers=other).status_code
        == 202
    )


def test_failpoints_are_refused_in_beta() -> None:
    with pytest.raises(ValueError, match="FIRSTCALL_FAILPOINT"):
        Settings(env="beta", failpoint="after_lead_insert")
    assert Settings(env="local", failpoint="after_lead_insert").failpoint == "after_lead_insert"
