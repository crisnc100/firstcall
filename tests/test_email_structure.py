"""Real-world email shapes the synthetic corpus does not have, and the parser's limits.

The corpus is single-part, ASCII, unfolded and LF-only, so a parser change can replay it byte
for byte and still break real mail. These fixtures pin the buyer fields for the shapes that
broke before: multipart bodies, attached messages, folded and encoded headers, CRLF and bare
CR line ends, and long headers.
"""

from __future__ import annotations

import signal
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress

import pytest
from sqlalchemy.orm import Session, sessionmaker

from firstcall.core import read
from firstcall.core.read import Reference, SourceRules
from tests.conftest import FakeClock
from tests.helpers import Pipeline

REFERENCE = Reference(("Juniper Mesa", "Copperleaf"), ())
INBOX = SourceRules("email", relay_senders=frozenset({"leads@hearthly.example"}))
HEAD = "From: Ana Ruiz <ana@inbox.example>\nTo: leads@larkspur.example\nSubject: Hello\n"


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


def buyer(raw: str | bytes) -> tuple[str | None, str | None, str | None, str | None]:
    data = raw.encode() if isinstance(raw, str) else raw
    reading = read.read(data, INBOX, REFERENCE)
    return reading.name, reading.email, reading.phone, reading.item


EXPECTED = ("Ana Ruiz", "ana@inbox.example", "+12025550142", "Juniper Mesa")


# The fixture set


def test_multipart_alternative_reads_the_plain_part() -> None:
    raw = (
        HEAD + "MIME-Version: 1.0\nContent-Type: multipart/alternative; boundary=b1\n\n"
        "--b1\nContent-Type: text/plain; charset=utf-8\n\n"
        "Is Juniper Mesa open?\n\nAna\n202-555-0142\n"
        "--b1\nContent-Type: text/html; charset=utf-8\n\n"
        "<p>Is Juniper Mesa open?</p><p>Ana</p><p>202-555-0142</p>\n--b1--\n"
    )
    assert buyer(raw) == EXPECTED


def test_x1_an_attached_message_never_lends_its_number() -> None:
    raw = (
        HEAD + "MIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=b1\n\n"
        "--b1\nContent-Type: text/html; charset=utf-8\n\n"
        "<p>Is Juniper Mesa open?</p><p>Ana</p><p>202-555-0142</p>\n"
        "--b1\nContent-Type: message/rfc822\nContent-Disposition: attachment\n\n"
        "From: Support <help@vendor.example>\nSubject: Old ticket\n"
        "Content-Type: text/plain\n\nCall support on 303-555-0177.\n--b1--\n"
    )
    assert buyer(raw) == EXPECTED


def test_x2_an_encoded_display_name_with_a_comma_keeps_name_and_address() -> None:
    raw = (
        "From: =?utf-8?q?Ru=C3=ADz=2C_Ana?= <ana@inbox.example>\nSubject: Hello\n\n"
        "Is Juniper Mesa open?\n\n202-555-0142\n"
    )
    assert buyer(raw) == ("Ruíz, Ana", "ana@inbox.example", "+12025550142", "Juniper Mesa")


def test_x4_a_folded_subject_keeps_its_item() -> None:
    raw = (
        "From: Ana Ruiz <ana@inbox.example>\r\nSubject: Juniper\r\n Mesa\r\n\r\n"
        "Is it open?\r\n\r\n202-555-0142\r\n"
    )
    assert buyer(raw) == EXPECTED


@pytest.mark.parametrize("ending", ["\r\n", "\r"])
def test_crlf_and_bare_cr_line_endings(ending: str) -> None:
    raw = (HEAD + "\nIs Juniper Mesa open?\n\nAna\n202-555-0142\n").replace("\n", ending)
    assert buyer(raw) == EXPECTED


def test_a_long_references_header_under_the_cap_is_read() -> None:
    ids = " ".join(f"<{n:06d}.thread@mail.example>" for n in range(130))
    assert len(ids) < read.MAX_LINE_CHARS
    folded = "\n ".join(ids.split(" "))
    raw = HEAD + f"References: {folded}\n\nIs Juniper Mesa open?\n\nAna\n202-555-0142\n"
    assert buyer(raw) == EXPECTED


# x3 and the structure limits: refused fast, with a reason


def _nested_content_type(semicolons: int) -> str:
    value = 'text/plain; a="' + ";" * semicolons + '"'
    return (
        HEAD + "MIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=b1\n\n"
        f"--b1\nContent-Type: {value}\n\nIs Juniper Mesa open?\n--b1--\n"
    )


def test_x3_a_huge_nested_part_header_is_refused_fast() -> None:
    with cpu_limit(2.0), pytest.raises(read.OverBudgetError):
        read.read(_nested_content_type(100_000).encode(), INBOX, REFERENCE)


def test_deeply_nested_messages_are_refused_not_crashed() -> None:
    layers = 1_000
    opening = "Content-Type: message/rfc822\n\n" * layers
    raw = HEAD + "MIME-Version: 1.0\n" + opening + "From: x@y.example\n\nhi\n"
    with cpu_limit(2.0), pytest.raises(read.OverBudgetError):
        read.read(raw.encode(), INBOX, REFERENCE)


def test_too_many_parts_are_refused() -> None:
    parts = "".join(
        f"--b1\nContent-Type: text/plain\n\npart {n}\n" for n in range(read.MAX_PARTS + 1)
    )
    raw = (
        HEAD
        + "MIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=b1\n\n"
        + parts
        + "--b1--\n"
    )
    with cpu_limit(2.0), pytest.raises(read.OverBudgetError, match="parts"):
        read.read(raw.encode(), INBOX, REFERENCE)


# Timing at the largest accepted caps, and just above them


def _many_legal_headers(total: int) -> str:
    each = read.MAX_LINE_CHARS - 20
    count = total // (each + 12)
    headers = "".join(f"X-Filler-{n}: {('a ' * each)[:each]}\n" for n in range(count))
    return headers + HEAD + "\nIs Juniper Mesa open?\n\nAna\n202-555-0142\n"


def _timed(raw: str) -> float:
    started = time.process_time()
    with cpu_limit(2.0), suppress(read.OverBudgetError):
        read.read(raw.encode(), INBOX, REFERENCE)
    return time.process_time() - started


def test_many_legal_headers_filling_the_budget_are_read_in_time() -> None:
    raw = _many_legal_headers(read.MAX_HEADER_BYTES - 1_000)
    assert buyer(raw) == EXPECTED
    assert _timed(raw) < 2.0


def test_headers_just_over_the_total_are_refused_fast() -> None:
    raw = _many_legal_headers(read.MAX_HEADER_BYTES + read.MAX_LINE_CHARS)
    with cpu_limit(2.0), pytest.raises(read.OverBudgetError, match="headers"):
        read.read(raw.encode(), INBOX, REFERENCE)


def test_a_nested_content_type_just_under_the_cap_is_read_in_time() -> None:
    raw = _nested_content_type(read.MAX_LINE_CHARS - 40)
    assert _timed(raw) < 2.0
    assert read.read(raw.encode(), INBOX, REFERENCE).item == "Juniper Mesa"


def test_a_nested_content_type_just_over_the_cap_is_refused_fast() -> None:
    with cpu_limit(2.0), pytest.raises(read.OverBudgetError):
        read.read(_nested_content_type(read.MAX_LINE_CHARS + 10).encode(), INBOX, REFERENCE)


def test_parts_nested_past_the_depth_limit_are_refused() -> None:
    layers = read.MAX_DEPTH + 5
    opening = "Content-Type: message/rfc822\n\n" * layers
    raw = HEAD + "MIME-Version: 1.0\n" + opening + "From: x@y.example\n\nhi\n"
    assert layers < read.MAX_PARTS
    with pytest.raises(read.OverBudgetError, match="nested"):
        read.read(raw.encode(), INBOX, REFERENCE)


def test_an_over_limit_email_ends_failed_with_its_reason_and_stays_stored(
    sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    pipe = Pipeline(sessions, clock)
    raw = _nested_content_type(100_000).encode()
    message = pipe.deliver("inbox", raw)
    assert message.status == "failed"
    assert message.reason is not None
    assert "a header of" in message.reason
    assert message.raw == raw
