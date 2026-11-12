"""Web push through pywebpush, signed with the server's VAPID key."""

from __future__ import annotations

import ipaddress
import json
import time
from collections.abc import Sequence
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests
from pywebpush import WebPushException, webpush

# What a sender reports when no answer came back: the push service is treated as down.
NO_RESPONSE = 0


def endpoint_allowed(endpoint: str, hosts: Sequence[str], allow_http: bool) -> bool:
    """Whether the server may POST to this push endpoint. A browser hands out endpoints on
    its vendor's push service, so anything else is someone pointing the worker at a host of
    their choosing: https on port 443, a host name (never an IP literal), no user info, and
    a host on `hosts`, where an entry starting with "." matches any subdomain of it.

    `allow_http` is the local and test escape hatch for the fake push service; settings
    refuse it in beta."""
    if allow_http:
        return urlsplit(endpoint).scheme in ("http", "https")
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or port not in (None, 443) or parts.username or not host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return False
    return any(
        host.endswith(entry) and len(host) > len(entry) if entry.startswith(".") else host == entry
        for entry in (h.lower() for h in hosts)
    )


class _BoundedSession(requests.Session):
    """A requests session whose POST stops reading after `max_bytes` or `deadline` seconds.
    requests buffers a whole response by default, so a push endpoint that streams forever
    would hold the worker or fill its memory; a push service's answer is a few bytes."""

    def __init__(self, deadline: float, max_bytes: int) -> None:
        super().__init__()
        self.deadline, self.max_bytes = deadline, max_bytes
        self.last_body_bytes = 0
        # A push service never needs a redirect, and following one would send the payload
        # to an address nobody subscribed.
        self.max_redirects = 0

    def post(self, url: str | bytes, data: Any = None, json: Any = None, **kwargs: Any) -> Any:
        kwargs["stream"] = True
        kwargs.setdefault("allow_redirects", False)
        stop = time.monotonic() + self.deadline
        response = super().post(url, data=data, json=json, **kwargs)
        body = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=4096):
                body.extend(chunk[: self.max_bytes - len(body)])
                if len(body) >= self.max_bytes or time.monotonic() >= stop:
                    break
        except requests.RequestException:
            pass
        finally:
            response.close()
        self.last_body_bytes = len(body)
        # pywebpush reads .text on an error answer; it gets the bytes read so far.
        response._content = bytes(body)
        return response


class PushSender(Protocol):
    def send(self, endpoint: str, p256dh: str, auth: str, payload: dict[str, Any]) -> int:
        """The push service's HTTP status, or NO_RESPONSE."""
        ...


class WebPushSender:
    def __init__(
        self, private_key: str, subject: str, timeout: float = 5.0, max_bytes: int = 16384
    ) -> None:
        self.private_key, self.subject, self.timeout = private_key, subject, timeout
        self.http = _BoundedSession(deadline=timeout, max_bytes=max_bytes)

    @property
    def last_body_bytes(self) -> int:
        return self.http.last_body_bytes

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
