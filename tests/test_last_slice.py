"""Regression tests for the last review slice of PR1a."""

from __future__ import annotations

import json
import signal
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy.orm import Session, sessionmaker

from firstcall.core import read
from firstcall.core.read import Reference, SourceRules
from firstcall.db import Source
from firstcall.intake.store import delivery_key
from tests.conftest import FakeClock
from tests.helpers import Pipeline

REFERENCE = Reference(("Juniper Mesa",), ())
INBOX = SourceRules("email", relay_senders=frozenset({"leads@hearthly.example"}))
RELAY_HEAD = "From: Hearthly <leads@hearthly.example>\nSubject: New inquiry\n"
EXPECTED = ("Ana Ruiz", "ana@inbox.example", "+12025550142", "Juniper Mesa")


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


def html_buyer(html: str) -> tuple[str | None, str | None, str | None, str | None]:
    raw = RELAY_HEAD + "Content-Type: text/html; charset=utf-8\n\n" + html
    reading = read.read(raw.encode(), INBOX, REFERENCE)
    return reading.name, reading.email, reading.phone, reading.item


# f1: HTML block boundaries survive into label extraction


@pytest.mark.parametrize(
    "html",
    [
        "<p>Name: Ana Ruiz</p><p>Email: ana@inbox.example</p><p>Phone: 202-555-0142</p>"
        "<p>Listing: Juniper Mesa</p>",
        "<p>Email: ana@inbox.example</p><p>Phone: 202-555-0142</p><p>Listing: Juniper Mesa</p>"
        "<p>Name: Ana Ruiz</p>",
        "<div>Name: Ana Ruiz<br>Email: ana@inbox.example<br/>Phone: 202-555-0142<br />"
        "Listing: Juniper Mesa</div>",
        "<table><tr><td>Name:</td><td>Ana Ruiz</td></tr><tr><td>Email:</td>"
        "<td>ana@inbox.example</td></tr><tr><td>Phone:</td><td>202-555-0142</td></tr>"
        "<tr><td>Listing:</td><td>Juniper Mesa</td></tr></table>",
    ],
    ids=["paragraphs", "paragraphs-email-first", "br-lines", "table-rows"],
)
def test_f1_html_only_portal_fields_stay_on_their_own_lines(html: str) -> None:
    assert html_buyer(html) == EXPECTED


# f2 and s1: real parts are counted, headers or not


def _headerless_parts(count: int) -> bytes:
    parts = "".join("--b1\n\nx\n" for _ in range(count))
    return (
        "From: Ana Ruiz <ana@inbox.example>\nSubject: hi\nMIME-Version: 1.0\n"
        "Content-Type: multipart/mixed; boundary=b1\n\n" + parts + "--b1--\n"
    ).encode()


def test_f2_s1_a_thousand_headerless_parts_are_refused() -> None:
    with cpu_limit(2.0), pytest.raises(read.OverBudgetError, match="parts"):
        read.read(_headerless_parts(1_000), INBOX, REFERENCE)


def test_f2_s1_a_near_cap_parent_type_with_many_headerless_parts_is_refused_fast() -> None:
    params = "; ".join(f"p{n}=v" for n in range(500))[: read.MAX_LINE_CHARS - 80]
    parts = "".join("--b1\n\nx\n" for _ in range(5_000))
    raw = (
        "From: A <a@b.example>\nSubject: hi\nMIME-Version: 1.0\n"
        f"Content-Type: multipart/mixed; boundary=b1; {params}\n\n" + parts + "--b1--\n"
    ).encode()
    with cpu_limit(2.0), pytest.raises(read.OverBudgetError, match="parts"):
        read.read(raw, INBOX, REFERENCE)


# f3: intake reads Message-ID past long unread headers


def _thread_mail(received: str) -> bytes:
    references = "\n ".join(f"<{n:06d}.thread@mail.example>" for n in range(800))
    return (
        f"Received: {received}\nFrom: Ana Ruiz <ana@inbox.example>\nSubject: hi\n"
        f"Message-ID: <lead-1@mail.example>\nReferences: {references}\n\nJuniper Mesa?\n"
    ).encode()


def test_f3_a_long_references_header_keeps_message_id_dedup() -> None:
    source = Source(kind="email", slug="inbox")
    first = delivery_key(source, _thread_mail("from relay-a"), {})
    again = delivery_key(source, _thread_mail("from relay-b"), {})
    assert first.key == "mid:<lead-1@mail.example>"
    assert again.key == first.key


# f4 and s3: a long Message-ID is hashed into the key and the message is stored


def test_f4_s3_a_long_message_id_is_stored_with_a_hashed_key(
    sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    pipe = Pipeline(sessions, clock)
    long_id = "<" + "a" * 3_000 + "@mail.example>"
    raw = f"From: Ana Ruiz <ana@inbox.example>\nMessage-ID: {long_id}\n\nJuniper Mesa?\n".encode()
    message_id = pipe.store("inbox", raw)
    stored = pipe.message(message_id)
    assert stored.delivery_key is not None
    assert len(stored.delivery_key) <= 100
    assert pipe.store("inbox", raw + b"\n") == message_id


# f5 and s2: deep nesting found after the parse still fails with a reason


@pytest.mark.parametrize("header", ["From", "Reply-To", "Content-Disposition"])
def test_f5_s2_deeply_nested_header_comments_are_over_budget(header: str) -> None:
    value = "(" * 2_000
    lines = {"From": "From: A <a@b.example>", "Reply-To": "", "Content-Disposition": ""}
    lines[header] = f"{header}: {value}"
    raw = "\n".join(line for line in lines.values() if line) + "\nSubject: x\n\nJuniper Mesa?\n"
    with pytest.raises(read.OverBudgetError):
        read.read(raw.encode(), INBOX, REFERENCE)


def test_f5_s2_a_deeply_nested_json_body_is_unparseable_not_a_crash() -> None:
    body = '{"a": ' + "[" * 2_000 + "]" * 2_000 + "}"
    assert json.loads(body)
    with pytest.raises(read.UnparseableError):
        read.read(body.encode(), SourceRules("form"), REFERENCE)


def test_f5_s2_a_nested_comment_email_ends_failed_on_the_first_attempt(
    sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    pipe = Pipeline(sessions, clock)
    raw = ("From: " + "(" * 2_000 + "\nSubject: x\n\nJuniper Mesa?\n").encode()
    message = pipe.deliver("inbox", raw)
    assert message.status == "failed"
    assert message.reason is not None
    assert "budget" in message.reason
