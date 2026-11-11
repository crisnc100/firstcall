"""Request limits: a sliding one-minute window per key, and a body read that stops at a cap.

The window lives in process memory, so it holds per api process. v0.1 runs one.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable

WINDOW_SECONDS = 60.0


class RateLimiter:
    def __init__(self, per_minute: int, now: Callable[[], float]) -> None:
        self.per_minute = per_minute
        self.now = now
        self.hits: defaultdict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        current = self.now()
        hits = self.hits[key]
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
