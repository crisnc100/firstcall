"""Regression tests for the fix-check findings on the PR1a review round."""

from __future__ import annotations

import re
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from firstcall.api import create_app
from firstcall.core import read
from firstcall.core.identity import find_emails, normalize_email
from firstcall.core.read import Reference, SourceRules
from firstcall.intake import limits
from firstcall.intake.limits import RateLimiter
from firstcall.seed import seed, set_secret
from firstcall.settings import Settings
from tests.conftest import FakeClock
from tests.helpers import Pipeline, email

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = Reference(("Juniper Mesa",), ())
INBOX = SourceRules("email", relay_senders=frozenset({"leads@hearthly.example"}))
PORTAL = (
    "New lead from Hearthly\n\nName: Ana Ruiz\nPhone: (202) 555-0142\n"
    "Email: ana.ruiz@inbox.example\nListing: Juniper Mesa\n"
)


@contextmanager
def cpu_limit(seconds: float) -> Iterator[None]:
    def expired(signum: int, frame: object) -> None:
        raise TimeoutError(f"over {seconds} CPU seconds")

    previous = signal.signal(signal.SIGPROF, expired)
    signal.setitimer(signal.ITIMER_PROF, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_PROF, 0)
        signal.signal(signal.SIGPROF, previous)


# 1c9500 x1: a blank line after the forward marker


@pytest.mark.parametrize(
    "marker", ["---------- Forwarded message ---------", "-----Original Message-----"]
)
def test_1c9500_x1_a_blank_line_after_the_forward_marker_keeps_the_buyer(marker: str) -> None:
    body = (
        f"FYI\n\n{marker}\n\nFrom: Hearthly <leads@hearthly.example>\n"
        "Date: Mon, Mar 2, 2026 at 8:10 AM\nSubject: x\n\n" + PORTAL
    )
    reading = read.read(email("Sam Agent <sam@larkspur.example>", body, "Fwd: x"), INBOX, REFERENCE)
    assert (reading.name, reading.email, reading.phone) == (
        "Ana Ruiz",
        "ana.ruiz@inbox.example",
        "+12025550142",
    )


# 1c9500 x2: a long HTML tag is removed whole, in linear time


def test_1c9500_x2_a_long_html_tag_never_lends_its_attributes() -> None:
    padding = "x" * 2_500
    html = (
        f'<div class="{padding}" data-support="202-555-0199"><p>Is Juniper Mesa open?</p>'
        "<p>Ana</p><p>303-555-0177</p></div>"
    )
    raw = (
        "From: Ana Ruiz <ana@inbox.example>\nSubject: Hi\nContent-Type: text/html\n\n" + html
    ).encode()
    reading = read.read(raw, INBOX, REFERENCE)
    assert reading.phone == "+13035550177"
    assert "data-support" not in reading.text


# 1c9500 x3: an address that starts with a quote is kept as written


@pytest.mark.parametrize("address", ["'ana@inbox.example", "`ana@inbox.example"])
def test_1c9500_x3_a_leading_quote_is_part_of_an_unwrapped_address(address: str) -> None:
    assert find_emails(f"write to {address} now") == [address]
    assert normalize_email(address) == address


def test_1c9500_x3_a_quote_led_address_never_joins_the_plain_address_lead(
    sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    pipe = Pipeline(sessions, clock)
    form = {"name": "Ana Ruiz", "email": "ana@inbox.example", "listing": "Juniper Mesa"}
    first = pipe.deliver_form("site-form", form)
    other = pipe.deliver_form(
        "site-form", {**form, "name": "Bo Lee", "email": "'ana@inbox.example"}
    )
    assert other.status == "lead_created"
    assert other.lead_id != first.lead_id


# 595516 x1: a full key table never wipes the requester's own history


def test_595516_x1_the_oldest_key_keeps_its_history_when_the_table_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(limits, "MAX_KEYS", 3)
    limiter = RateLimiter(1, lambda: 0.0)
    for key in ("a", "b", "c"):
        assert limiter.allow(key)
    assert not limiter.allow("a")
    assert not limiter.allow("a")


# 595516 x2: a per-IP 429 still carries CORS, and the form shows the failure


def test_595516_x2_a_per_ip_429_carries_the_cors_header(
    sessions: sessionmaker[Session], clock: FakeClock, database_url: str
) -> None:
    seed(sessions)
    token = set_secret(sessions, "site-form")
    settings = Settings(database_url=database_url, env="test", ip_rate_per_minute=1)
    client = TestClient(create_app(settings, sessions, clock, lambda: 0.0))
    origin = "https://www.larkspur.example"
    headers = {"Content-Type": "application/json", "Origin": origin}
    assert (
        client.post(f"/intake/site-form/{token}", content=b"{}", headers=headers).status_code == 202
    )
    limited = client.post(f"/intake/site-form/{token}", content=b'{"n": 1}', headers=headers)
    assert limited.status_code == 429
    assert limited.headers["access-control-allow-origin"] == origin
    other = client.post(
        f"/intake/site-form/{token}",
        content=b'{"n": 2}',
        headers={**headers, "Origin": "https://evil.example"},
    )
    assert other.status_code == 429
    assert "access-control-allow-origin" not in other.headers


def test_595516_x2_the_example_form_reports_a_refused_request() -> None:
    script = (ROOT / "examples" / "form.html").read_text()
    assert re.search(r"catch\s*(\(\w*\))?\s*\{[^}]*Please try again", script)
