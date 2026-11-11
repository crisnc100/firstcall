"""Delivery keys and the cleaned body that spots a re-forward."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from firstcall.core import delivery

PORTAL = "New lead from Hearthly\n\nName: Ana Ruiz\nPhone: 202-555-0142\n\nMessage:\nHi!"


def test_email_keys_use_the_message_id_or_the_raw_hash() -> None:
    assert delivery.for_email(b"raw", " <a@b> ").key == "mid:<a@b>"
    by_hash = delivery.for_email(b"raw", None)
    assert by_hash.key == f"sha:{delivery.sha256(b'raw')}"
    assert not by_hash.windowed
    assert delivery.for_email(b"raw", "  ").key == by_hash.key


def test_json_keys_use_the_delivery_id_or_a_windowed_body_hash() -> None:
    assert delivery.for_json(b"{}", "dlv-1").key == "id:dlv-1"
    windowed = delivery.for_json(b"{}", None)
    assert windowed.key is None
    assert windowed.windowed
    assert windowed.body_sha256 == delivery.sha256(b"{}")
    assert delivery.for_json(b"{}", " ").windowed


def test_a_gmail_style_forward_cleans_to_the_original_body() -> None:
    forward = (
        "Can someone grab this one?\n\n---------- Forwarded message ---------\n"
        "From: Hearthly <leads@hearthly.example>\nDate: Mon, Mar 2, 2026 at 9:14 AM\n"
        "Subject: New inquiry\nTo: <leads@larkspur.example>\n\n" + PORTAL
    )
    assert delivery.clean_body(forward) == delivery.clean_body(PORTAL)
    assert delivery.clean_body_sha256(forward) == delivery.clean_body_sha256(PORTAL)


def test_an_outlook_style_forward_with_quoting_cleans_to_the_original_body() -> None:
    quoted = "\n".join(f"> {line}" for line in PORTAL.splitlines())
    forward = (
        "-----Original Message-----\nFrom: Hearthly <leads@hearthly.example>\n"
        "Sent: Monday, March 2, 2026 9:14 AM\nTo: leads@larkspur.example\n"
        "Subject: New inquiry\n\n" + quoted
    )
    assert delivery.clean_body(forward) == delivery.clean_body(PORTAL)


def test_a_different_body_stays_different() -> None:
    assert delivery.clean_body(PORTAL) != delivery.clean_body(PORTAL.replace("Ana", "Bea"))


@given(st.text())
def test_cleaning_is_idempotent_and_never_raises(text: str) -> None:
    once = delivery.clean_body(text)
    assert delivery.clean_body(once) == once
