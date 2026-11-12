"""Postgres advisory locks keyed by text, held until the transaction ends."""

from __future__ import annotations

import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session


def lock_id(*parts: str) -> int:
    digest = hashlib.sha256("\x1f".join(parts).encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def lock_all(session: Session, keys: list[tuple[str, ...]]) -> None:
    """Take every lock in sorted order, so two transactions never wait on each other in a
    cycle."""
    for value in sorted({lock_id(*key) for key in keys}):
        session.execute(select(func.pg_advisory_xact_lock(value)))
