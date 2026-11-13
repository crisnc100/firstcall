"""Chrome's Beta, Dev and Canary channels subscribe on jmt17.google.com, so that host is on
the default list, matched exactly (docs review bcc95e f4)."""

from __future__ import annotations

import pytest

from firstcall.outbound.push import checked_endpoint
from firstcall.settings import Settings

HOSTS = Settings(env="test").push_hosts


def test_a_chrome_beta_dev_or_canary_endpoint_is_accepted() -> None:
    endpoint = "https://jmt17.google.com/fcm/send/abc"
    assert checked_endpoint(endpoint, HOSTS, allow_http=False) == endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://jmt17.google.com.evil.example/fcm/send/abc",
        "https://evil.jmt17.google.com/fcm/send/abc",
        "https://jmt17.google.com.:443/fcm/send/abc",
        "https://jmt17-google.com/fcm/send/abc",
    ],
)
def test_a_jmt17_lookalike_is_refused(endpoint: str) -> None:
    assert checked_endpoint(endpoint, HOSTS, allow_http=False) is None
