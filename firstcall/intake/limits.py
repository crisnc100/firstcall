"""Request limits: a sliding one-minute window per key, and a body read that stops at a cap.

The window lives in process memory, so it holds per api process. v0.1 runs one.
"""

from __future__ import annotations

import ipaddress
from collections import deque
from collections.abc import AsyncIterator, Callable

WINDOW_SECONDS = 60.0
MAX_KEYS = 100_000
MAX_KEY_CHARS = 64


def ip_key(address: str) -> str:
    """The rate-limit key for a client address. An IPv6 client usually holds a whole /64,
    so the key is that /64; text that is not an address is cut to a short key."""
    try:
        parsed = ipaddress.ip_address(address.strip())
    except ValueError:
        return address[:MAX_KEY_CHARS]
    if isinstance(parsed, ipaddress.IPv6Address):
        if parsed.ipv4_mapped is not None:
            return str(parsed.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{parsed}/64", strict=False))
    return str(parsed)


class RateLimiter:
    def __init__(self, per_minute: int, now: Callable[[], float]) -> None:
        self.per_minute = per_minute
        self.now = now
        self.hits: dict[str, deque[float]] = {}
        self.last_sweep = now()

    def _sweep(self, current: float) -> None:
        """Forget every key with no hit in the window, so idle keys cost no memory."""
        for key in [k for k, hits in self.hits.items() if current - hits[-1] >= WINDOW_SECONDS]:
            del self.hits[key]
        self.last_sweep = current

    def allow(self, key: str) -> bool:
        current = self.now()
        if current - self.last_sweep >= WINDOW_SECONDS or len(self.hits) >= MAX_KEYS:
            self._sweep(current)
        key = key[:MAX_KEY_CHARS]
        while key not in self.hits and len(self.hits) >= MAX_KEYS:
            # Still full after the sweep: make room for a new key by dropping the key first
            # seen longest ago. A key already in the table keeps its history.
            del self.hits[next(iter(self.hits))]
        hits = self.hits.setdefault(key, deque())
        while hits and current - hits[0] >= WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return False
        hits.append(current)
        return True


class BodyTooLargeError(Exception):
    pass


async def read_limited(stream: AsyncIterator[bytes], max_bytes: int) -> bytes:
    """The whole body. Raises BodyTooLargeError as soon as it passes max_bytes, unread past that."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in stream:
        total += len(chunk)
        if total > max_bytes:
            raise BodyTooLargeError
        chunks.append(chunk)
    return b"".join(chunks)
