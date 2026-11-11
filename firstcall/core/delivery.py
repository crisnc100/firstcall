"""Delivery keys: how a repeat delivery of the same message is spotted, and the cleaned body
that spots a re-forward.

- Email: the source plus the Message-ID header, or the SHA-256 of the raw message when there
  is no Message-ID.
- Form and call service: the source plus the provider's delivery id header, when the source
  names one. Otherwise the SHA-256 of the body, but only within WINDOW, because a buyer can
  genuinely send the same form twice.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta

WINDOW = timedelta(minutes=10)
REFORWARD_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class DeliveryKey:
    # Unique per source when set. None means "match the same body within WINDOW".
    key: str | None
    body_sha256: str

    @property
    def windowed(self) -> bool:
        return self.key is None


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def for_email(raw: bytes, message_id: str | None) -> DeliveryKey:
    digest = sha256(raw)
    cleaned = (message_id or "").strip()
    return DeliveryKey(f"mid:{cleaned}" if cleaned else f"sha:{digest}", digest)


def for_json(body: bytes, delivery_id: str | None) -> DeliveryKey:
    cleaned = (delivery_id or "").strip()
    return DeliveryKey(f"id:{cleaned}" if cleaned else None, sha256(body))


_FORWARD_MARKER = re.compile(
    r"^-{2,}\s*(forwarded message|original message)\s*-{2,}\s*$", re.IGNORECASE | re.MULTILINE
)
_HEADER_LINE = re.compile(r"^(from|date|sent|to|cc|subject):", re.IGNORECASE)


def clean_body(text: str) -> str:
    """The text a re-forward shares with the original: everything after the last forward
    header block, with quoting and spacing removed."""
    body = text
    markers = list(_FORWARD_MARKER.finditer(body))
    if markers:
        rest = body[markers[-1].end() :].splitlines()
        index = 0
        while index < len(rest) and (not rest[index].strip() or _HEADER_LINE.match(rest[index])):
            index += 1
        body = "\n".join(rest[index:])
    lines = [line.lstrip("> ").strip() for line in body.splitlines()]
    return " ".join(" ".join(line for line in lines if line).split()).casefold()


def clean_body_sha256(text: str) -> str:
    return sha256(clean_body(text).encode())
