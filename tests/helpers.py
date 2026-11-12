"""Building blocks for pipeline tests: store a message as intake would, then run the worker."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import Lead, LeadEvent, Message, Source
from firstcall.intake.store import accept
from firstcall.pipeline.jobs import run_one
from firstcall.seed import seed
from tests.conftest import FakeClock


def email(
    sender: str,
    body: str,
    subject: str = "Hello",
    message_id: str | None = None,
    date: str | None = "Mon, 02 Mar 2026 09:00:00 -0500",
    reply_to: str | None = None,
) -> bytes:
    lines = [f"From: {sender}", "To: leads@larkspur.example", f"Subject: {subject}"]
    if date:
        lines.append(f"Date: {date}")
    lines.append(f"Message-ID: {message_id or f'<{uuid.uuid4().hex}@mail.example>'}")
    if reply_to:
        lines.append(f"Reply-To: {reply_to}")
    return ("\n".join(lines) + "\n\n" + body + "\n").encode()


@dataclass
class Pipeline:
    sessions: sessionmaker[Session]
    clock: FakeClock

    def __post_init__(self) -> None:
        seed(self.sessions)

    def source(self, slug: str) -> Source:
        with self.sessions() as session:
            return session.scalars(select(Source).where(Source.slug == slug)).one()

    def store(self, slug: str, raw: bytes, headers: dict[str, str] | None = None) -> uuid.UUID:
        source = self.source(slug)
        content_type = "message/rfc822" if source.kind == "email" else "application/json"
        return accept(self.sessions, source, raw, content_type, headers or {}).message_id

    def form(
        self, slug: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> uuid.UUID:
        return self.store(slug, json.dumps(payload).encode(), headers)

    def run(self) -> int:
        ran = 0
        while run_one(self.sessions, self.clock):
            ran += 1
        return ran

    def deliver(self, slug: str, raw: bytes, headers: dict[str, str] | None = None) -> Message:
        message_id = self.store(slug, raw, headers)
        self.run()
        return self.message(message_id)

    def deliver_form(
        self, slug: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> Message:
        return self.deliver(slug, json.dumps(payload).encode(), headers)

    def message(self, message_id: uuid.UUID) -> Message:
        with self.sessions() as session:
            return session.get_one(Message, message_id)

    def lead(self, lead_id: uuid.UUID | None) -> Lead:
        assert lead_id is not None
        with self.sessions() as session:
            return session.get_one(Lead, lead_id)

    def leads(self) -> list[Lead]:
        with self.sessions() as session:
            return list(session.scalars(select(Lead).order_by(Lead.created_at)))

    def events(self, lead_id: uuid.UUID) -> list[LeadEvent]:
        with self.sessions() as session:
            return list(
                session.scalars(
                    select(LeadEvent).where(LeadEvent.lead_id == lead_id).order_by(LeadEvent.seq)
                )
            )

    def count(self, model: Any) -> int:
        with self.sessions() as session:
            return session.scalar(select(func.count()).select_from(model)) or 0

    def team_slug(self, lead: Lead) -> str | None:
        from firstcall.db import Team

        if lead.team_id is None:
            return None
        with self.sessions() as session:
            return session.get_one(Team, lead.team_id).slug
