"""Regression tests for the PR1a review findings. Each names its finding id."""

from __future__ import annotations

import base64
import contextlib
import json
import re
import signal
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import func, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.api import create_app
from firstcall.core import delivery, identity, match, place, read
from firstcall.core.read import AgentRef, Reference, SourceRules
from firstcall.corpus.generate import GOLD_DIR, SEEDS
from firstcall.db import Lead, Message, Source
from firstcall.evaluation import run
from firstcall.evaluation.run import Result, score
from firstcall.intake import auth
from firstcall.intake.limits import RateLimiter
from firstcall.pipeline.process import process
from firstcall.seed import seed, set_secret
from firstcall.settings import Settings
from tests.conftest import FakeClock
from tests.helpers import Pipeline, email

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = Reference(("Juniper Mesa", "Copperleaf"), ())
INBOX = SourceRules("email", relay_senders=frozenset({"leads@hearthly.example"}))
PORTAL = (
    "New lead from Hearthly\n\nName: Ana Ruiz\nPhone: (202) 555-0142\n"
    "Email: ana.ruiz@inbox.example\nListing: Juniper Mesa\n\nMessage:\nIs it available?"
)


class FakeMonotonic:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


@contextlib.contextmanager
def cpu_limit(seconds: float) -> Iterator[None]:
    """Stop the block with TimeoutError after this much CPU time, so a slow pattern fails the
    test instead of hanging it."""

    def expired(signum: int, frame: object) -> None:
        raise TimeoutError(f"over {seconds} CPU seconds")

    previous = signal.signal(signal.SIGPROF, expired)
    signal.setitimer(signal.ITIMER_PROF, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_PROF, 0)
        signal.signal(signal.SIGPROF, previous)


@pytest.fixture
def pipe(sessions: sessionmaker[Session], clock: FakeClock) -> Pipeline:
    return Pipeline(sessions, clock)


# 174a3f f1: hard negatives must compete inside the matching pool


def _same_pool_peers(seed_: int) -> dict[str, int]:
    labels = [json.loads(x) for x in (GOLD_DIR / f"seed-{seed_}" / "labels.jsonl").open()]
    messages = [json.loads(x) for x in (GOLD_DIR / f"seed-{seed_}" / "messages.jsonl").open()]
    earlier: list[tuple[dict[str, Any], dict[str, Any]]] = []
    counts: dict[str, int] = dict.fromkeys(
        ("household_phone", "shared_agent_phone", "portal_relay_sender", "call_tracking_number"), 0
    )
    unqualified: list[str] = []

    def key(label: dict[str, Any], message: dict[str, Any], kind: str) -> str | None:
        if kind == "household_phone":
            return label["phone"]
        if kind == "shared_agent_phone":
            return (label["agent"] or {}).get("phone")
        if kind == "portal_relay_sender":
            return label["layout"] if label["layout"].startswith("portal-") else None
        if message["source"] == "calls":
            return json.loads(message["body"]).get("tracking_number")
        return None

    for label, message in zip(labels, messages, strict=True):
        for kind in counts:
            if kind not in label["hard_negatives"]:
                continue
            mine = key(label, message, kind)
            peers = [
                other
                for other, other_message in earlier
                if other["lead"] != label["lead"]
                and key(other, other_message, kind) == mine
                and (label["team"] is None or other["team"] == label["team"])
            ]
            if peers:
                counts[kind] += 1
            else:
                unqualified.append(f"{label['id']} {kind}")
        if label["category"] == "lead":
            earlier.append((label, message))
    counts["unqualified"] = len(unqualified)
    return counts


@pytest.mark.parametrize("seed_", SEEDS)
def test_174a3f_f1_every_hard_negative_has_an_earlier_peer_in_its_matching_pool(seed_: int) -> None:
    counts = _same_pool_peers(seed_)
    assert counts.pop("unqualified") == 0
    assert all(value >= 20 for value in counts.values()), counts


# 1c9500 s1: bounded input and linear patterns


def _patterns() -> list[tuple[str, re.Pattern[str]]]:
    modules: list[ModuleType] = [read, identity, delivery, match, place]
    found: list[tuple[str, re.Pattern[str]]] = []
    for module in modules:
        for name, value in vars(module).items():
            if isinstance(value, re.Pattern):
                found.append((f"{module.__name__}.{name}", value))
    return found


def _fillers(width: int) -> list[str]:
    half = width // 2
    return [
        " " * half + "a" + " " * (width - half - 1),
        "a" + " " * (width - 2) + "b",
        "|" + " " * (width - 2) + "a",
        "<" * width,
        "-" * width,
        "a" * width,
        "A" + "a" * (width - 1),
        ("Aa " * width)[:width],
        ("a." * width)[:width],
        ("a@" * width)[:width],
        ("Name" + " " * (width - 4)),
        ("1-" * width)[:width],
    ]


def test_1c9500_s1_the_read_budget_is_defined() -> None:
    assert getattr(read, "MAX_LINE_CHARS", 0) >= 500
    assert getattr(read, "MAX_LINES", 0) >= 500
    assert getattr(read, "MAX_TEXT_CHARS", 0) >= 20_000
    assert issubclass(getattr(read, "OverBudgetError", object), read.UnparseableError)


def test_1c9500_s1_every_core_pattern_is_fast_at_the_line_cap() -> None:
    width = getattr(read, "MAX_LINE_CHARS", 2_000)
    lines = getattr(read, "MAX_LINES", 3_000)
    slow: list[str] = []
    for name, pattern in _patterns():
        for filler in _fillers(width):
            body = "\n".join([filler] * max(1, min(lines, 50)))
            started = time.process_time()
            try:
                with cpu_limit(0.5):
                    for _ in pattern.finditer(body):
                        pass
            except TimeoutError:
                slow.append(f"{name} on {filler[:12]!r}")
                continue
            if time.process_time() - started > 0.5:
                slow.append(f"{name} on {filler[:12]!r}")
    assert slow == []


def test_1c9500_s1_the_rules_read_is_fast_on_adversarial_text_at_the_cap() -> None:
    width = getattr(read, "MAX_LINE_CHARS", 2_000)
    lines = getattr(read, "MAX_LINES", 3_000)
    text_cap = getattr(read, "MAX_TEXT_CHARS", 100_000)
    # Each fixture adds a few characters and lines of its own, so the body leaves room for
    # them: every read must run to the end, inside the budget.
    room = 100
    for filler in _fillers(width):
        body = "\n".join([filler] * (lines - 5))[: text_cap - room]
        for raw, rules in (
            (email("A <a@b.example>", body), INBOX),
            (email("Hearthly <leads@hearthly.example>", body), INBOX),
            (email("A <a@b.example>", "My client Ana Ruiz\n" + body), INBOX),
            (json.dumps({"name": "x", "message": body}).encode(), SourceRules("form")),
        ):
            with cpu_limit(2.0):
                reading = read.read(raw, rules, REFERENCE)
            assert reading.category in ("lead", "spam")


def test_1c9500_s1_an_over_budget_message_fails_with_a_reason(pipe: Pipeline) -> None:
    line = " " * 20_000 + "a" + " " * 20_000
    with cpu_limit(2.0):
        message = pipe.deliver("inbox", email("Mallory <m@x.example>", line))
    assert message.status == "failed"
    assert message.reason is not None
    assert "budget" in message.reason
    assert pipe.count(Lead) == 0


# 1c9500 f1: CRLF forwards


@pytest.mark.parametrize(
    "marker",
    [
        "---------- Forwarded message ---------\r\nFrom: Hearthly <leads@hearthly.example>\r\n"
        "Date: Mon, Mar 2, 2026 at 8:10 AM\r\nSubject: x\r\n",
        "-----Original Message-----\r\nFrom: Hearthly <leads@hearthly.example>\r\n"
        "Sent: Monday, March 2, 2026 8:10 AM\r\nSubject: x\r\n",
    ],
)
def test_1c9500_f1_a_crlf_forward_keeps_its_buyer_fields(marker: str) -> None:
    body = "FYI\n\n" + marker.replace("\r\n", "\n") + "\n" + PORTAL
    raw = email("Sam <sam@larkspur.example>", body, "Fwd: x").replace(b"\n", b"\r\n")
    assert b"\r\r" not in raw
    assert raw.count(b"\r\n") == raw.count(b"\n")
    reading = read.read(raw, INBOX, REFERENCE)
    assert (reading.name, reading.phone, reading.email, reading.item) == (
        "Ana Ruiz",
        "+12025550142",
        "ana.ruiz@inbox.example",
        "Juniper Mesa",
    )
    assert reading.source_time is not None


# 1c9500 f2: email normalisation keeps the whole address


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("o'connor@example.com", "o'connor@example.com"),
        ("buyer!sales@example.com", "buyer!sales@example.com"),
    ],
)
def test_1c9500_f2_an_address_is_never_shortened(text: str, expected: str) -> None:
    assert identity.normalize_email(text) == expected
    assert identity.find_emails(text) == [expected]


# 1c9500 f3: a "-- " signature belongs to the agent


def test_1c9500_f3_a_dash_dash_signature_is_the_agents(pipe: Pipeline) -> None:
    def agent_mail(client: str) -> bytes:
        body = f"My client {client} would like to see Copperleaf.\n-- \nCorinne Vale\n512-555-0113"
        return email("Corinne Vale <corinne@vale.example>", body)

    reading = read.read(agent_mail("Ana Ruiz"), INBOX, REFERENCE)
    assert reading.phone is None
    assert reading.agent == AgentRef("Corinne Vale", "corinne@vale.example", "+15125550113")
    first = pipe.deliver("inbox", agent_mail("Ana Ruiz"))
    second = pipe.deliver("inbox", agent_mail("Ana Vega"))
    assert second.status == "lead_created"
    assert second.lead_id != first.lead_id


# 1c9500 f4: re-forward cleaning stops at the end of the forward header block


def test_1c9500_f4_a_body_starting_with_a_from_line_cleans_the_same_forwarded() -> None:
    original = "From: Ana Ruiz <ana@example.com>\nTel: 202-555-0142\n\nInterested in Copperleaf."
    forward = (
        "---------- Forwarded message ---------\nFrom: Dwellr <notify@dwellr.example>\n"
        "Date: Mon, Mar 2, 2026 at 9:14 AM\nSubject: x\n\n" + original
    )
    assert delivery.clean_body(forward) == delivery.clean_body(original)


# 595516 f2: the lead keeps its earliest arrival


def test_595516_f2_a_later_processed_earlier_message_moves_arrival_back(pipe: Pipeline) -> None:
    body = "Is Copperleaf open?\n\nAna\n202-555-0142"
    early = pipe.store("inbox", email("Ana Ruiz <ana@inbox.example>", body))
    late = pipe.store("inbox", email("Ana Ruiz <ana@inbox.example>", body + "\nPS"))
    with pipe.sessions.begin() as session:
        session.execute(
            update(Message)
            .where(Message.id == early)
            .values(received_at=func.clock_timestamp() - timedelta(minutes=5))
        )
    for message_id in (late, early):
        with pipe.sessions.begin() as session:
            process(session, message_id, pipe.clock)
    first = pipe.message(early)
    assert first.status == "attached"
    assert pipe.lead(first.lead_id).arrival_at == first.received_at


# 595516 f3: the re-forward window has an upper bound


def test_595516_f3_a_match_received_25_hours_later_is_not_a_re_forward(pipe: Pipeline) -> None:
    raw_early = email("Hearthly <leads@hearthly.example>", PORTAL, message_id="<e@x>")
    raw_late = email("Hearthly <leads@hearthly.example>", PORTAL, message_id="<l@x>")
    early = pipe.store("inbox", raw_early)
    late = pipe.store("inbox", raw_late)
    with pipe.sessions.begin() as session:
        session.execute(
            update(Message)
            .where(Message.id == early)
            .values(received_at=func.clock_timestamp() - timedelta(hours=25))
        )
    for message_id in (late, early):
        with pipe.sessions.begin() as session:
            process(session, message_id, pipe.clock)
    assert pipe.message(early).status == "attached"


# Intake fixtures


PARTNER_SECRET = "partner-test-secret"  # pragma: allowlist secret


def _app(
    sessions: sessionmaker[Session],
    clock: FakeClock,
    database_url: str,
    monotonic: FakeMonotonic | None = None,
    **overrides: Any,
) -> TestClient:
    settings = Settings(database_url=database_url, env="test", **overrides)
    return TestClient(create_app(settings, sessions, clock, monotonic or FakeMonotonic()))


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# 595516 f4: non-ASCII auth values answer 401 or authenticate, never 500


def test_595516_f4_a_non_ascii_basic_user_authenticates(
    sessions: sessionmaker[Session], clock: FakeClock, database_url: str
) -> None:
    seed(sessions)
    password = set_secret(sessions, "inbox")
    with sessions.begin() as session:
        session.query(Source).filter(Source.slug == "inbox").update({"basic_user": "josé"})
    client = _app(sessions, clock, database_url)
    headers = {"Content-Type": "message/rfc822", **_basic("josé", password)}
    assert (
        client.post(
            "/intake/inbox", content=b"From: a@b.example\n\nhi", headers=headers
        ).status_code
        == 202
    )
    wrong = {"Content-Type": "message/rfc822", **_basic("josë", password)}
    assert (
        client.post("/intake/inbox", content=b"From: a@b.example\n\nhi", headers=wrong).status_code
        == 401
    )


def test_595516_f4_a_non_ascii_signature_is_401(
    sessions: sessionmaker[Session],
    clock: FakeClock,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRSTCALL_PARTNER_FORM_SECRET", PARTNER_SECRET)
    seed(sessions)
    client = _app(sessions, clock, database_url)
    headers = [
        (b"content-type", b"application/json"),
        (auth.TIMESTAMP_HEADER.lower().encode(), str(int(clock.now().timestamp())).encode()),
        (auth.SIGNATURE_HEADER.lower().encode(), "sha256=é".encode()),
    ]
    response = client.post("/intake/partner-form", content=b"{}", headers=headers)
    assert response.status_code == 401


# 595516 s1: a bad credential never spends the source's budget


def test_595516_s1_failed_auth_does_not_block_the_real_sender(
    sessions: sessionmaker[Session], clock: FakeClock, database_url: str
) -> None:
    seed(sessions)
    password = set_secret(sessions, "inbox")
    client = _app(sessions, clock, database_url, ip_rate_per_minute=1000)
    headers = {"Content-Type": "message/rfc822"}
    for _ in range(60):
        bad = {**headers, **_basic("inbox", "guess")}
        assert client.post("/intake/inbox", content=b"x", headers=bad).status_code == 401
    good = {**headers, **_basic("inbox", password)}
    assert (
        client.post("/intake/inbox", content=b"From: a@b.example\n\nhi", headers=good).status_code
        == 202
    )


# 595516 s2: behind a proxy, the client address is the right-most entry


def test_595516_s2_rotating_the_left_forwarded_entry_does_not_escape_the_limit(
    sessions: sessionmaker[Session], clock: FakeClock, database_url: str
) -> None:
    seed(sessions)
    token = set_secret(sessions, "site-form")
    client = _app(sessions, clock, database_url, trust_proxy=True)
    codes = [
        client.post(
            f"/intake/site-form/{token}",
            content=f'{{"n": {n}}}'.encode(),
            headers={
                "Content-Type": "application/json",
                "X-Forwarded-For": f"198.51.100.{n}, 203.0.113.7",
            },
        ).status_code
        for n in range(31)
    ]
    assert codes[:30] == [202] * 30
    assert codes[30] == 429


def test_595516_s2_an_unparseable_forwarded_entry_falls_back_to_the_peer(
    sessions: sessionmaker[Session], clock: FakeClock, database_url: str
) -> None:
    seed(sessions)
    token = set_secret(sessions, "site-form")
    client = _app(sessions, clock, database_url, trust_proxy=True)
    codes = [
        client.post(
            f"/intake/site-form/{token}",
            content=f'{{"n": {n}}}'.encode(),
            headers={"Content-Type": "application/json", "X-Forwarded-For": f"junk-{n}"},
        ).status_code
        for n in range(31)
    ]
    assert codes[30] == 429


# 595516 s3: the rate limiter forgets idle keys


def test_595516_s3_idle_rate_limit_keys_are_evicted() -> None:
    clock = FakeMonotonic()
    limiter = RateLimiter(5, clock)
    for n in range(1000):
        limiter.allow(f"addr-{n}")
    clock.value += 61
    limiter.allow("someone")
    assert len(limiter.hits) <= 1


def test_595516_s3_ipv6_addresses_share_a_bucket_per_64() -> None:
    from firstcall.intake.limits import ip_key

    key: Callable[[str], str] = ip_key
    assert key("2001:db8:1:2::1") == key("2001:db8:1:2:ffff::9")
    assert key("2001:db8:1:3::1") != key("2001:db8:1:2::1")
    assert key("203.0.113.9") == "203.0.113.9"
    assert len(key("x" * 10_000)) <= 64


# 948fda f1: the example form can post from another origin


def test_948fda_f1_cors_preflight_and_post_for_an_allowed_origin(
    sessions: sessionmaker[Session], clock: FakeClock, database_url: str
) -> None:
    seed(sessions)
    token = set_secret(sessions, "site-form")
    client = _app(sessions, clock, database_url)
    origin = "https://www.larkspur.example"
    preflight = client.options(
        f"/intake/site-form/{token}",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code in (200, 204)
    assert preflight.headers["access-control-allow-origin"] == origin
    assert "POST" in preflight.headers["access-control-allow-methods"]
    assert "content-type" in preflight.headers["access-control-allow-headers"].lower()
    posted = client.post(
        f"/intake/site-form/{token}",
        content=b'{"name": "Ana"}',
        headers={"Content-Type": "application/json", "Origin": origin},
    )
    assert posted.status_code == 202
    assert posted.headers["access-control-allow-origin"] == origin
    other = client.options(
        f"/intake/site-form/{token}",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in other.headers


# 948fda f2: compose passes the partner-form secret to the api


def test_948fda_f2_compose_passes_the_hmac_secret_to_the_api() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    environment = compose["services"]["api"]["environment"]
    names = (
        set(environment)
        if isinstance(environment, dict)
        else {e.split("=")[0] for e in environment}
    )
    assert "FIRSTCALL_PARTNER_FORM_SECRET" in names


# 595516 f1 and 575e84 s1: the real server trusts no proxy header and logs no token


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_595516_f1_575e84_s1_the_server_ignores_forwarded_headers_and_logs_no_token(
    sessions: sessionmaker[Session],
    clock: FakeClock,
    database_url: str,
    capfd: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    import uvicorn

    from firstcall import cli

    seed(sessions)
    token = set_secret(sessions, "site-form")
    app = create_app(Settings(database_url=database_url, env="test"), sessions, clock)
    port = _free_port()
    config: uvicorn.Config = cli.server_config(app, "127.0.0.1", port)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    caplog.set_level("DEBUG")
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        codes = [
            httpx.post(
                f"http://127.0.0.1:{port}/intake/site-form/{token}",
                content=f'{{"n": {n}}}'.encode(),
                headers={"Content-Type": "application/json", "X-Forwarded-For": f"198.51.100.{n}"},
            ).status_code
            for n in range(31)
        ]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert codes[:30] == [202] * 30
    assert codes[30] == 429
    captured = capfd.readouterr()
    assert token not in captured.out + captured.err
    # The test's own HTTP client logs the URLs it calls; only the server's records count.
    server_records = [
        record.getMessage()
        for record in caplog.records
        if not record.name.startswith(("httpx", "httpcore"))
    ]
    # The server's own log lines reach the captured output, so their absence means something.
    assert "Application startup complete" in captured.out + captured.err
    assert not any(token in message for message in server_records)


# 177f72 f1: a safe split is not a wrong attach


def _label(id_: str, **values: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": id_,
        "category": "lead",
        "name": "Ana Ruiz",
        "phone": "+12025550142",
        "email": None,
        "item": "Copperleaf",
        "agent": None,
        "team": "west",
        "lead": "L1",
        "case": "new",
        "layout": "call",
        "seen": True,
        "hard_negatives": [],
        "injection": None,
    }
    return {**base, **values}


def _result(id_: str, lead: str, created: bool) -> Result:
    extracted = {
        "name": "Ana Ruiz",
        "phone": "+12025550142",
        "email": None,
        "item": "Copperleaf",
        "agent": None,
    }
    status = "lead_created" if created else "attached"
    return Result(id_, status, "why", "lead", extracted, "west", lead, created)


def test_177f72_f1_a_repeat_joining_a_safe_duplicate_is_not_a_wrong_attach() -> None:
    labels = [_label("m1"), _label("m2", case="repeat"), _label("m3", case="repeat")]
    results = [_result("m1", "S1", True), _result("m2", "S2", True), _result("m3", "S2", False)]
    matching = score(results, labels)["splits"]["seen"]["matching"]
    assert matching["wrong_attaches"] == {"count": 0, "of": 2}
    assert matching["recall"]["hits"] == 1


# 177f72 f2: seed 13 needs the field-method choice committed and unchanged


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_177f72_f2_seed_13_needs_a_committed_unchanged_field_methods_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git(tmp_path, "init", "-q")
    _git(
        tmp_path,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )
    methods = tmp_path / "config" / "field_methods.yaml"
    methods.parent.mkdir()
    methods.write_text("phone: rules\n")
    monkeypatch.setattr(run, "FIELD_METHODS", methods)
    monkeypatch.setattr(run, "REPO_ROOT", tmp_path, raising=False)
    with pytest.raises(RuntimeError, match="committed"):
        run.evaluate(13, "postgresql+psycopg://unused/x")
    _git(tmp_path, "add", "config/field_methods.yaml")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "methods")
    methods.write_text("phone: model\n")
    with pytest.raises(RuntimeError, match="committed"):
        run.evaluate(13, "postgresql+psycopg://unused/x")
