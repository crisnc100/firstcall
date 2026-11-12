"""Regression tests for two findings deferred from PR1a: HTML line breaks in a forward (x1) and
bare-CR header lines at intake (x2)."""

from __future__ import annotations

from datetime import datetime

import pytest

from firstcall.core import read
from firstcall.core.read import Reference, SourceRules

REFERENCE = Reference(("Juniper Mesa",), ())
INBOX = SourceRules("email", relay_senders=frozenset({"leads@hearthly.example"}))


def _html_forward(gap: str, ending: str = "\n") -> bytes:
    """A forwarded relay lead as an HTML-only email. Each forwarded header ends in a br tag
    and a source line break, as mail clients write them."""
    html = (
        "<div>FYI</div><br>\n"
        "---------- Forwarded message ---------<br>\n"
        f"{gap}"
        "From: Hearthly &lt;leads@hearthly.example&gt;<br>\n"
        "Date: Mon, Mar 2, 2026 at 8:10 AM<br>\n"
        "To: Sales &lt;sales@larkspur.example&gt;<br>\n"
        "Reply-To: Ana Ruiz &lt;ana.ruiz@inbox.example&gt;<br>\n"
        "Subject: New lead<br>\n"
        "<br>\n"
        "<div>Name: Ana Ruiz</div>\n"
        "<div>Listing: Juniper Mesa</div>\n"
        "<div>Phone: (202) 555-0142</div>\n"
    )
    head = (
        "From: Sam Agent <sam@larkspur.example>\nTo: leads@larkspur.example\n"
        "Subject: Fwd: New lead\nDate: Mon, 02 Mar 2026 09:00:00 -0500\n"
        "MIME-Version: 1.0\nContent-Type: text/html; charset=utf-8\n\n"
    )
    return (head + html).replace("\n", ending).encode()


# x1: an HTML line break never ends the forwarded header block early


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_x1_br_line_breaks_keep_the_forwarded_reply_to(ending: str) -> None:
    reading = read.read(_html_forward("", ending), INBOX, REFERENCE)
    assert reading.email == "ana.ruiz@inbox.example"
    assert reading.phone == "+12025550142"
    assert reading.item == "Juniper Mesa"
    assert reading.source_time is not None
    assert reading.source_time.replace(tzinfo=None) == datetime(2026, 3, 2, 8, 10)


def test_x1_a_real_blank_line_after_the_marker_still_keeps_the_buyer() -> None:
    reading = read.read(_html_forward("<br>\n"), INBOX, REFERENCE)
    assert reading.email == "ana.ruiz@inbox.example"
    assert reading.email != "sam@larkspur.example"


def test_x1_a_double_br_still_ends_the_forwarded_header_block() -> None:
    # A br on a line of its own ends the forwarded headers, so the labelled body after it is
    # read as the lead and not swallowed into the header block.
    reading = read.read(_html_forward(""), INBOX, REFERENCE)
    assert reading.name == "Ana Ruiz"
    assert "name" not in reading.unsure


def test_x1_source_line_breaks_inside_html_text_are_spaces() -> None:
    text = read._strip_tags("<p>Name:\n  Ana Ruiz</p>\n<p>Phone: 202-555-0142</p>")
    assert read._labels(text) == {"name": "Ana Ruiz", "phone": "202-555-0142"}
