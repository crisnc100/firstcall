"""The raw store: a message and its job are written in one transaction before any 2xx."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from email import policy
from email.parser import BytesHeaderParser
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from firstcall.core import delivery
from firstcall.db import Job, Message, Source
from firstcall.locks import lock_all


@dataclass(frozen=True)
class Accepted:
    status: str
    message_id: uuid.UUID


def delivery_key(source: Source, raw: bytes, headers: Mapping[str, str]) -> delivery.DeliveryKey:
    if source.kind == "email":
        parsed = BytesHeaderParser(policy=policy.default).parsebytes(raw)
        message_id = parsed.get("Message-ID")
        return delivery.for_email(raw, str(message_id) if message_id else None)
    lowered = {name.lower(): value for name, value in headers.items()}
    header = (source.delivery_id_header or "").lower()
    delivery_id = lowered.get(header) if header else None
    return delivery.for_json(raw, delivery_id)


def _existing(session: Session, source: Source, key: delivery.DeliveryKey) -> uuid.UUID | None:
    if key.key is not None:
        return session.scalar(
            select(Message.id).where(
                Message.source_id == source.id, Message.delivery_key == key.key
            )
        )
    window = f"{int(delivery.WINDOW.total_seconds())} seconds"
    return session.scalar(
        select(Message.id)
        .where(
            Message.source_id == source.id,
            Message.delivery_key.is_(None),
            Message.body_sha256 == key.body_sha256,
            Message.received_at > text("clock_timestamp() - CAST(:window AS interval)"),
        )
        .params(window=window)
        .order_by(Message.received_at.desc())
        .limit(1)
    )


def add_job(session: Session, message: Message) -> None:
    session.add(Job(org_id=message.org_id, message_id=message.id, kind="process"))


def accept(
    sessions: sessionmaker[Any],
    source: Source,
    raw: bytes,
    content_type: str,
    headers: Mapping[str, str],
) -> Accepted:
    key = delivery_key(source, raw, headers)
    with sessions.begin() as session:
        lock_all(session, [("delivery", str(source.id), key.key or key.body_sha256)])
        existing = _existing(session, source, key)
        if existing is not None:
            return Accepted("duplicate_delivery", existing)
        message = Message(
            id=uuid.uuid4(),
            org_id=source.org_id,
            source_id=source.id,
            content_type=content_type,
            raw=raw,
            body_sha256=key.body_sha256,
            delivery_key=key.key,
            status="received",
        )
        session.add(message)
        session.flush()
        add_job(session, message)
    return Accepted("received", message.id)
