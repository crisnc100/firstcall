"""The rate limiter's window, its key table cap, and client address keys."""

from __future__ import annotations

import pytest

from firstcall.core.identity import find_emails
from firstcall.intake import limits
from firstcall.intake.limits import RateLimiter, ip_key


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_old_hits_leave_the_window_while_newer_ones_stay() -> None:
    clock = Clock()
    limiter = RateLimiter(2, clock)
    assert limiter.allow("a")
    clock.value = 30
    assert limiter.allow("a")
    assert not limiter.allow("a")
    clock.value = 61
    assert limiter.allow("a")
    assert not limiter.allow("a")


def test_a_full_key_table_drops_the_oldest_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_KEYS", 3)
    limiter = RateLimiter(1, Clock())
    for key in ("a", "b", "c", "d"):
        assert limiter.allow(key)
    assert list(limiter.hits) == ["b", "c", "d"]
    assert limiter.allow("a")


def test_an_ipv4_mapped_ipv6_address_keys_as_ipv4() -> None:
    assert ip_key("::ffff:203.0.113.9") == "203.0.113.9"


def test_a_lone_quote_before_the_at_sign_is_not_an_address() -> None:
    assert find_emails("'@inbox.example") == []
