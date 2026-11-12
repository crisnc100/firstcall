"""The reread job: once the model is back, read again the messages that took the rules path.

It only fills a lead's empty fields. It never changes the lead's state or team, so it never
closes a lead or moves an owned one, and it never touches a field a person edited. When the
model says a message that made a lead is not a lead, the lead keeps its place and gets a
flag for the monitor. A filled phone or email becomes the lead's identity only when no other
lead holds it, so a model answer can never merge two buyers.

Only messages that created a lead are reread. An attached message was never gated on its
category and its lead's fields came from the message that created it.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session, aliased, sessionmaker

from firstcall.core.model_read import combine
from firstcall.core.read import UnparseableError, read
from firstcall.db import Identity, Lead, Message, ModelCall, Source
from firstcall.locks import lock_all
from firstcall.model.gateway import DOWN, NotSyntheticError, ReplayMissError
from firstcall.pipeline.model_read import UNREADABLE, ModelReader
from firstcall.pipeline.process import identities, org_view, source_rules

log = logging.getLogger("firstcall.reread")
BATCH = 50
# Answers, readable or not, that a message gets before it stops being reread. A failed
# attempt (the model down) is not an answer, so an outage never uses this up.
MAX_CALLS_PER_MESSAGE = 4
# Rereads that raised (a replay miss, a database error) before a message is left alone.
MAX_REREAD_ERRORS = 3
# Failed attempts since a message's last answer before it is set aside, counted only while
# the model answers other messages. Each failure keeps its budget reservation, so a message
# the provider always rejects cannot keep reserving budget. An outage fails every message
# alike and sets none aside; the fewest-failures-first order keeps one message from holding
# the head of the queue.
MAX_TRANSPORT_FAILURES = 10
SET_ASIDE = "set_aside"
# Errors that come from the mode or the database, not from the message: a missing recording,
# record mode given real input, a lost connection or a deadlock. They end the tick and count
# against no message.
MODE_ERRORS: tuple[type[Exception], ...] = (
    ReplayMissError,
    NotSyntheticError,
    OperationalError,
    InterfaceError,
)
FILLABLE = ("name", "phone", "email", "item")


def _since_answer() -> Any:
    """Failed attempts logged for the outer message since its last answer."""
    answers = aliased(ModelCall)
    answered = (
        select(func.max(answers.created_at))
        .where(answers.message_id == Message.id, answers.ok.is_(True))
        .scalar_subquery()
    )
    return select(ModelCall).where(
        ModelCall.message_id == Message.id,
        ModelCall.ok.is_(False),
        or_(answered.is_(None), ModelCall.created_at > answered),
    )


def _failures_since_answer() -> Any:
    return _since_answer().with_only_columns(func.count()).scalar_subquery()


def _due(session: Session, limit: int) -> list[uuid.UUID]:
    calls = (
        select(func.count())
        .select_from(ModelCall)
        .where(ModelCall.message_id == Message.id, ModelCall.ok.is_(True))
        .scalar_subquery()
    )
    failures = _failures_since_answer()
    return list(
        session.scalars(
            select(Message.id)
            .where(
                Message.needs_reread.is_(True),
                Message.status == "lead_created",
                calls < MAX_CALLS_PER_MESSAGE,
                Message.reread_errors < MAX_REREAD_ERRORS,
                or_(Message.model_unavailable.is_(None), Message.model_unavailable != SET_ASIDE),
            )
            .order_by(failures, Message.received_at, Message.id)
            .limit(limit)
        )
    )


def _set_aside_if_stuck(sessions: sessionmaker[Any], message_id: uuid.UUID) -> None:
    """Set the message aside when it has failed often since its last answer and the model
    has answered some other call since its first such failure."""
    with sessions.begin() as session:
        message = session.get_one(Message, message_id)
        failed = list(
            session.scalars(
                _since_answer().where(Message.id == message_id).order_by(ModelCall.created_at)
            )
        )
        if len(failed) < MAX_TRANSPORT_FAILURES:
            return
        others_answered = session.scalar(
            select(func.count())
            .select_from(ModelCall)
            .where(
                ModelCall.org_id == message.org_id,
                ModelCall.ok.is_(True),
                ModelCall.created_at > failed[0].created_at,
            )
        )
        if others_answered:
            message.model_unavailable = SET_ASIDE


def _holders(session: Session, lead: Lead, kind: str, value: str) -> set[uuid.UUID]:
    return set(
        session.scalars(
            select(Identity.lead_id).where(
                Identity.org_id == lead.org_id, Identity.kind == kind, Identity.value == value
            )
        )
    )


def _reread_one(session: Session, message_id: uuid.UUID, reader: ModelReader) -> str:
    """What happened to one message: filled, unchanged, unreadable, skipped, or a gateway
    reason when the model is unavailable again."""
    message = session.scalar(
        select(Message)
        .where(Message.id == message_id, Message.needs_reread.is_(True))
        .with_for_update(key_share=True, skip_locked=True)
    )
    if message is None or message.lead_id is None:
        return "skipped"
    source = session.get_one(Source, message.source_id)
    view = org_view(session, message.org_id, source)
    try:
        rules = read(message.raw, source_rules(source), view.reference)
    except UnparseableError:
        return "skipped"
    outcome = reader.read(message, source.kind, rules, view.reference.items, view.team_labels)
    if outcome.answer is None:
        message.model_unavailable = outcome.reason
        return outcome.reason or UNREADABLE
    reading = combine(rules, outcome.answer, reader.methods)
    lead = session.get_one(Lead, message.lead_id, with_for_update=True)
    keys = dict(identities(reading, view))
    lock_all(session, [("identity", str(lead.org_id), k, v) for k, v in sorted(keys.items())])
    edited = {str(f) for f in lead.edited_fields}
    filled: list[str] = []
    for name in FILLABLE:
        if getattr(lead, name) is not None or name in edited:
            continue
        value: Any = keys.get(name) if name in ("phone", "email") else getattr(reading, name)
        if not value:
            continue
        if name in ("phone", "email"):
            holders = _holders(session, lead, name, value)
            if holders - {lead.id}:
                continue
            # A later message attached to this lead may already have added the identity
            # without setting the lead's column.
            if not holders:
                session.add(Identity(org_id=lead.org_id, lead_id=lead.id, kind=name, value=value))
        setattr(lead, name, value)
        filled.append(name)
    if reading.category != "lead":
        flag = f"reread_not_a_lead:{reading.category}"
        if flag not in lead.flags:
            lead.flags = [*lead.flags, flag]
    message.needs_reread = False
    message.model_unavailable = None
    message.model_answer = outcome.answer.as_json()
    return "filled" if filled else "unchanged"


def _record_error(sessions: sessionmaker[Any], message_id: uuid.UUID, error: Exception) -> None:
    with sessions.begin() as session:
        session.execute(
            update(Message)
            .where(Message.id == message_id)
            .values(
                reread_errors=Message.reread_errors + 1,
                model_unavailable=f"error: {type(error).__name__}"[:32],
            )
        )


def reread(sessions: sessionmaker[Any], reader: ModelReader, limit: int = BATCH) -> dict[str, int]:
    """Reread up to `limit` messages. Stops at the first sign the model is unavailable. A
    message whose reread raises is logged, counted and skipped, so it cannot stop the
    scheduler or hold up the others; after MAX_REREAD_ERRORS it is left alone."""
    with sessions() as session:
        due = _due(session, limit)
    counts: dict[str, int] = {}
    for message_id in due:
        try:
            with sessions.begin() as session:
                org_id = session.scalar(select(Message.org_id).where(Message.id == message_id))
                if org_id is None or reader.gateway.unavailable(org_id) is not None:
                    break
                result = _reread_one(session, message_id, reader)
        except MODE_ERRORS:
            log.exception("reread stopped at message %s; no message is charged", message_id)
            counts["halted"] = counts.get("halted", 0) + 1
            break
        except Exception as error:
            log.exception("reread of message %s failed", message_id)
            _record_error(sessions, message_id, error)
            result = "error"
        counts[result] = counts.get(result, 0) + 1
        if result == DOWN:
            _set_aside_if_stuck(sessions, message_id)
        if result not in ("filled", "unchanged", "skipped", "error", UNREADABLE):
            break
    return counts
