"""UUIDv7: a 48-bit millisecond timestamp followed by random bits (RFC 9562).

Python 3.12 has no uuid7, and event ids must sort by creation time.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime


def uuid7(at: datetime) -> uuid.UUID:
    millis = int(at.timestamp() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    rand_a = rand >> 68  # 12 bits
    rand_b = rand & ((1 << 62) - 1)
    value = (millis << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)
