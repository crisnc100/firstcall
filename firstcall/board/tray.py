"""The Not-a-lead tray: vendor, support and spam messages, and the monitor's rescue.

A rescue reads the message again from its stored bytes and its stored model answer, with no
new model call, then runs it through placement and matching as a lead. It is the one way a
message leaves a terminal status.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace

from sqlalchemy.orm import Session

from firstcall.board.leads import ConflictError, NotFoundError
from firstcall.core.access import Viewer
from firstcall.core.clock import Clock
from firstcall.core.model_read import combine, parse_answer
from firstcall.core.read import UnparseableError, read
from firstcall.db import Message, Source
from firstcall.pipeline.model_read import load_field_methods
from firstcall.pipeline.process import org_view, settle, source_rules


def may_see_tray(viewer: Viewer) -> bool:
    return viewer.monitor or viewer.admin


def rescue(session: Session, viewer: Viewer, message_id: uuid.UUID, clock: Clock) -> Message:
    if not may_see_tray(viewer):
        raise NotFoundError
    message = session.get(Message, message_id, with_for_update=True)
    if message is None or str(message.org_id) != viewer.org_id:
        raise NotFoundError
    if message.status != "not_a_lead":
        raise ConflictError("only a message in the Not-a-lead tray can be rescued")
    source = session.get_one(Source, message.source_id)
    view = org_view(session, message.org_id, source)
    try:
        reading = read(message.raw, source_rules(source), view.reference)
    except UnparseableError as error:
        raise ConflictError("the message can no longer be read") from error
    if message.model_answer:
        answer = parse_answer(
            json.dumps(message.model_answer), view.reference.items, sorted(view.team_labels)
        )
        if answer is not None:
            reading = combine(reading, answer, load_field_methods())
    reading = replace(reading, category="lead")
    message.category = "lead"
    message.extracted = reading.as_json()
    settle(session, message, source, view, reading, clock, uuid.UUID(viewer.user_id))
    return message
