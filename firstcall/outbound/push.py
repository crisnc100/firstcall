"""Web push through pywebpush, signed with the server's VAPID key."""

from __future__ import annotations

import json
from typing import Any, Protocol

import requests
from pywebpush import WebPushException, webpush

# What a sender reports when no answer came back: the push service is treated as down.
NO_RESPONSE = 0


class PushSender(Protocol):
    def send(self, endpoint: str, p256dh: str, auth: str, payload: dict[str, Any]) -> int:
        """The push service's HTTP status, or NO_RESPONSE."""
        ...


class WebPushSender:
    def __init__(self, private_key: str, subject: str, timeout: float = 5.0) -> None:
        self.private_key, self.subject, self.timeout = private_key, subject, timeout
        self.http = requests.Session()
        # A push service never needs a redirect, and following one would send the payload
        # to an address nobody subscribed.
        self.http.max_redirects = 0

    def send(self, endpoint: str, p256dh: str, auth: str, payload: dict[str, Any]) -> int:
        try:
            response = webpush(
                subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
                data=json.dumps(payload),
                vapid_private_key=self.private_key,
                vapid_claims={"sub": self.subject},
                timeout=self.timeout,
                ttl=3600,
                requests_session=self.http,
            )
        except WebPushException as error:
            return error.response.status_code if error.response is not None else NO_RESPONSE
        except requests.RequestException:
            return NO_RESPONSE
        return getattr(response, "status_code", NO_RESPONSE)
