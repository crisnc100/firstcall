"""Regression tests for the final slice review of PR1a."""

from __future__ import annotations

import signal
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy.orm import Session, sessionmaker

from firstcall.core import read
from firstcall.core.identity import find_emails, normalize_email
from firstcall.core.read import Reference, SourceRules
from firstcall.db import Source
from firstcall.intake.store import delivery_key
from tests.conftest import FakeClock
from tests.helpers import Pipeline, email

REFERENCE = Reference(("Juniper Mesa",), ())
INBOX = SourceRules("email", relay_senders=frozenset({"leads@hearthly.example"}))


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


# a0840f f1: a domain is read whole or not at all


def test_a0840f_f1_a_punycode_domain_is_never_shortened() -> None:
    assert normalize_email("ana@foo.com.xn--p1ai") == "ana@foo.com.xn--p1ai"
    assert find_emails("write ana@foo.com.xn--p1ai today") == ["ana@foo.com.xn--p1ai"]


def test_a0840f_f1_the_two_addresses_make_two_leads(
    sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    pipe = Pipeline(sessions, clock)
    form = {"name": "Ana Ruiz", "email": "ana@foo.com", "listing": "Juniper Mesa"}
    first = pipe.deliver_form("site-form", form)
    other = pipe.deliver_form(
        "site-form", {**form, "name": "Bo Lee", "email": "ana@foo.com.xn--p1ai"}
    )
    assert other.status == "lead_created"
    assert other.lead_id != first.lead_id


# a0840f f2: a '>' inside a quoted attribute or a comment does not end the tag


@pytest.mark.parametrize(
    "hidden",
    [
        '<div data-tooltip="a > b" data-support="202-555-0199">',
        "<div data-tooltip='a > b' data-support='202-555-0199'>",
        "<!-- a > b data-support 202-555-0199 --><div>",
    ],
)
def test_a0840f_f2_a_hidden_number_after_a_quoted_or_commented_bracket_stays_hidden(
    hidden: str,
) -> None:
    html = hidden + "<p>Is Juniper Mesa open?</p><p>Ana</p><p>303-555-0177</p></div>"
    raw = "From: Ana Ruiz <ana@inbox.example>\nSubject: Hi\nContent-Type: text/html\n\n" + html
    reading = read.read(raw.encode(), INBOX, REFERENCE)
    assert reading.phone == "+13035550177"
    assert "0199" not in reading.text


# a0840f f3: a forward takes Reply-To from the forwarded message, never the forwarder


def test_a0840f_f3_a_forward_reads_reply_to_from_the_inner_message() -> None:
    body = (
        "FYI\n\n---------- Forwarded message ---------\n"
        "From: Hearthly <leads@hearthly.example>\nReply-To: ana@inbox.example\n"
        "Date: Mon, Mar 2, 2026 at 8:10 AM\nSubject: x\n\n"
        "New lead from Hearthly\n\nName: Ana Ruiz\nListing: Juniper Mesa\n"
    )
    raw = email("Sam <sam@agency.example>", body, "Fwd: x", reply_to="sam@agency.example")
    reading = read.read(raw, INBOX, REFERENCE)
    assert reading.email == "ana@inbox.example"


def test_a0840f_f3_a_forward_without_an_inner_reply_to_never_takes_the_outer_one() -> None:
    body = (
        "---------- Forwarded message ---------\nFrom: Hearthly <leads@hearthly.example>\n"
        "Subject: x\n\nNew lead from Hearthly\n\nName: Ana Ruiz\n"
    )
    raw = email("Sam <sam@agency.example>", body, "Fwd: x", reply_to="sam@agency.example")
    assert read.read(raw, INBOX, REFERENCE).email is None


# a0840f s1: headers are inside the read budget, before any parse


def _words(size: int) -> str:
    return ("a " * size)[:size]


@pytest.mark.parametrize("header", ["Subject", "From"])
def test_a0840f_s1_a_huge_header_is_refused_quickly(header: str) -> None:
    value = _words(1_000_000) if header == "Subject" else f"{_words(1_000_000)} <a@b.example>"
    raw = f"{header}: {value}\n{'From: A <a@b.example>' if header == 'Subject' else 'Subject: x'}"
    raw += "\n\nhello\n"
    with cpu_limit(2.0), pytest.raises(read.OverBudgetError):
        read.read(raw.encode(), INBOX, REFERENCE)


def test_a0840f_s1_a_folded_huge_header_is_refused_quickly() -> None:
    folded = "\n ".join(_words(70) for _ in range(15_000))
    raw = f"From: A <a@b.example>\nSubject: {folded}\n\nhello\n".encode()
    with cpu_limit(2.0), pytest.raises(read.OverBudgetError):
        read.read(raw, INBOX, REFERENCE)


def test_a0840f_s1_intake_keys_a_huge_header_message_quickly() -> None:
    source = Source(kind="email", slug="inbox")
    raw = f"Message-ID: {_words(1_000_000)}\nSubject: x\n\nhello\n".encode()
    with cpu_limit(2.0):
        key = delivery_key(source, raw, {})
    assert key.key is not None
    assert key.key.startswith("sha:")


def test_a0840f_f1_a_domain_with_too_many_labels_is_rejected_not_cut() -> None:
    domain = ".".join(["a"] * 12) + ".example"
    assert find_emails(f"ana@{domain}") == []


@pytest.mark.parametrize("filler", ['<a x="', "<!--", "<a x='>", '<a x=">"> '])
def test_a0840f_f2_the_quote_aware_scan_stays_fast_at_the_budget(filler: str) -> None:
    line = (filler * read.MAX_LINE_CHARS)[: read.MAX_LINE_CHARS]
    body = "\n".join([line] * (read.MAX_LINES - 1))[: read.MAX_TEXT_CHARS - 100]
    raw = ("From: A <a@b.example>\nSubject: x\nContent-Type: text/html\n\n" + body).encode()
    with cpu_limit(2.0):
        read.read(raw, INBOX, REFERENCE)


def test_a0840f_s1_ordinary_headers_still_parse() -> None:
    raw = email("Ana Ruiz <ana@inbox.example>", "Juniper Mesa?", "=?utf-8?q?Caf=C3=A9_visit?=")
    reading = read.read(raw, INBOX, REFERENCE)
    assert (reading.name, reading.email, reading.item) == (
        "Ana Ruiz",
        "ana@inbox.example",
        "Juniper Mesa",
    )
