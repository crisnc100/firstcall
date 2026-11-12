"""Regression tests for the docs-focus review: HTML character entities."""

from __future__ import annotations

import pytest

from firstcall.core import read
from firstcall.core.read import Reference, SourceRules

REFERENCE = Reference(("Juniper Mesa",), ())
INBOX = SourceRules("email", relay_senders=frozenset({"leads@hearthly.example"}))


def html_buyer(html: str) -> tuple[str | None, str | None, str | None, str | None]:
    raw = (
        "From: Hearthly <leads@hearthly.example>\nSubject: New inquiry\n"
        "Content-Type: text/html; charset=utf-8\n\n" + html
    )
    reading = read.read(raw.encode(), INBOX, REFERENCE)
    return reading.name, reading.email, reading.phone, reading.item


@pytest.mark.parametrize("space", ["&nbsp;", "&#160;", "&#xA0;", "\N{NO-BREAK SPACE}"])
def test_html_entities_and_no_break_spaces_are_decoded(space: str) -> None:
    html = (
        f"<p>Name: Ana{space}Ruiz</p><p>Email: ana@inbox.example</p>"
        f"<p>Phone: (202){space}555-0142</p><p>Listing: Juniper{space}Mesa</p>"
    )
    assert html_buyer(html) == ("Ana Ruiz", "ana@inbox.example", "+12025550142", "Juniper Mesa")


def test_named_entities_in_a_name_are_decoded() -> None:
    html = "<p>Name: Zo&euml; O&#39;Brien</p><p>Email: zoe@inbox.example</p>"
    assert html_buyer(html)[0] == "Zoë O'Brien"
