"""Ten messages from one new person, processed at the same moment, make one lead (F8)."""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import Lead, LeadEvent, Message
from firstcall.pipeline.process import process
from tests.conftest import FakeClock
from tests.helpers import Pipeline

RUNS = 20
MESSAGES = 10


@pytest.fixture
def pipe(sessions: sessionmaker[Session], clock: FakeClock) -> Pipeline:
    return Pipeline(sessions, clock)


def _process_at_once(
    pipe: Pipeline, message_id: uuid.UUID, start: threading.Barrier, errors: list[Exception]
) -> None:
    try:
        start.wait()
        with pipe.sessions.begin() as session:
            process(session, message_id, pipe.clock)
    except Exception as error:
        errors.append(error)


def test_ten_concurrent_messages_from_one_new_person_make_one_lead(pipe: Pipeline) -> None:
    for run in range(RUNS):
        phone = f"202-555-01{run:02d}"
        address = f"buyer{run}@inbox.example"
        ids = [
            pipe.form(
                "site-form",
                {
                    "name": "Ana Ruiz",
                    "phone": phone,
                    "email": address,
                    "listing": "Juniper Mesa",
                    "message": f"note {n}",
                },
            )
            for n in range(MESSAGES)
        ]
        start = threading.Barrier(MESSAGES)
        errors: list[Exception] = []
        threads = [
            threading.Thread(target=_process_at_once, args=(pipe, i, start, errors)) for i in ids
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        with pipe.sessions() as session:
            leads = session.scalar(
                select(func.count()).select_from(Lead).where(Lead.email == address)
            )
            statuses = sorted(session.scalars(select(Message.status).where(Message.id.in_(ids))))
            created = session.scalar(
                select(func.count()).select_from(LeadEvent).where(LeadEvent.type == "lead.created")
            )
        assert leads == 1, f"run {run}"
        assert statuses == ["attached"] * (MESSAGES - 1) + ["lead_created"], f"run {run}"
        assert created == run + 1
