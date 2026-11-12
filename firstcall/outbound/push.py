"""Web push through pywebpush, signed with the server's VAPID key."""

from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
from collections.abc import Sequence
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests
from pywebpush import WebPushException, webpush
from urllib3.exceptions import LocationParseError
from urllib3.util import parse_url

# What a sender reports when no answer came back: the push service is treated as down.
NO_RESPONSE = 0


# Printable ASCII only, and none of the characters parsers disagree on: a backslash ends
# a host for urllib3 but not for urlsplit, and "@" starts user info.
_PLAIN_URL = re.compile(r"[!-~]+")
_REFUSED = set("\\@")


def checked_endpoint(endpoint: str, hosts: Sequence[str], allow_http: bool) -> str | None:
    """The URL to POST to, rebuilt from checked parts, or None when the server must not
    send there. A browser hands out endpoints on its vendor's push service, so anything
    else is someone pointing the worker at a host of their choosing: https on port 443, a
    host name (never an IP literal), and a host on `hosts`, where an entry starting with
    "." matches any subdomain of it. The host must read the same to urlsplit and to
    urllib3, which requests sends with, and the send uses the rebuilt URL, never the
    stored string.

    `allow_http` is the local and test escape hatch for the fake push service: any http or
    https host and port, still held to the same characters and the same reading. Settings
    refuse it in beta."""
    if not _PLAIN_URL.fullmatch(endpoint) or _REFUSED & set(endpoint):
        return None
    try:
        parts = urlsplit(endpoint)
        port = parts.port
        other = parse_url(endpoint)
    except (ValueError, LocationParseError):
        return None
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    if not host or (other.host or "").lower() != host or (other.port or None) != port:
        return None
    if allow_http:
        if scheme not in ("http", "https"):
            return None
        netloc = f"[{host}]" if ":" in host else host
        netloc += f":{port}" if port is not None else ""
    else:
        if scheme != "https" or port not in (None, 443) or _is_ip(host):
            return None
        if not any(_host_matches(host, entry.lower()) for entry in hosts):
            return None
        netloc = host
    query = f"?{parts.query}" if parts.query else ""
    return f"{scheme}://{netloc}{parts.path or '/'}{query}"


def endpoint_allowed(endpoint: str, hosts: Sequence[str], allow_http: bool) -> bool:
    return checked_endpoint(endpoint, hosts, allow_http) is not None


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _host_matches(host: str, entry: str) -> bool:
    if entry.startswith("."):
        return host.endswith(entry) and len(host) > len(entry)
    return host == entry


class _BoundedSession(requests.Session):
    """A requests session whose POST reads the answer a byte at a time and stops at
    `max_bytes` or `deadline` seconds. requests buffers a whole response by default, and a
    buffered read waits for its full chunk, so an endpoint that streams forever, or
    trickles a byte at a time, would hold the worker or fill its memory; a push service's
    answer is a few bytes."""

    def __init__(self, deadline: float, max_bytes: int) -> None:
        super().__init__()
        self.deadline, self.max_bytes = deadline, max_bytes
        self.last_body_bytes = 0
        # A push service never needs a redirect, and following one would send the payload
        # to an address nobody subscribed.
        self.max_redirects = 0

    def post(self, url: str | bytes, data: Any = None, json: Any = None, **kwargs: Any) -> Any:
        stop = time.monotonic() + self.deadline
        wait = min(1.0, self.deadline)
        kwargs.update(stream=True, allow_redirects=False, timeout=(wait, wait))
        response = super().post(url, data=data, json=json, **kwargs)
        body = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=1):
                body.extend(chunk)
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
        self.max_bytes = max_bytes
        self.last_body_bytes = 0

    def send(self, endpoint: str, p256dh: str, auth: str, payload: dict[str, Any]) -> int:
        """At most `timeout` seconds, whatever the endpoint does. The send runs in its own
        thread with its own session: a server that trickles its headers can hold that
        thread past the deadline, but never the worker, which counts it as no response."""
        http = _BoundedSession(deadline=self.timeout, max_bytes=self.max_bytes)
        result = [NO_RESPONSE]

        def run() -> None:
            result[0] = self._send(http, endpoint, p256dh, auth, payload)
            http.close()

        worker = threading.Thread(target=run, name="push-send", daemon=True)
        worker.start()
        worker.join(self.timeout)
        if worker.is_alive():
            return NO_RESPONSE
        self.last_body_bytes = http.last_body_bytes
        return result[0]

    def _send(
        self,
        http: _BoundedSession,
        endpoint: str,
        p256dh: str,
        auth: str,
        payload: dict[str, Any],
    ) -> int:
        try:
            response = webpush(
                subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
                data=json.dumps(payload),
                vapid_private_key=self.private_key,
                vapid_claims={"sub": self.subject},
                timeout=self.timeout,
                ttl=3600,
                requests_session=http,
            )
        except WebPushException as error:
            return error.response.status_code if error.response is not None else NO_RESPONSE
        except requests.RequestException:
            return NO_RESPONSE
        return getattr(response, "status_code", NO_RESPONSE)
