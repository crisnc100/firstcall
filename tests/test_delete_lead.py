"""Delete lead (F35): every table is accounted for, the buyer is gone, the event history keeps
its order, and a provider's retry cannot bring the lead back."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board.escalate import escalate
from firstcall.board.followups import run_nightly
from firstcall.db import (
    Activity,
    Alert,
    Base,
    Contact,
    Escalation,
    Followup,
    Identity,
    InboxEmail,
    Job,
    Lead,
    LeadProfile,
    Message,
    ModelCall,
    Note,
    Outcome,
    PushSend,
)
from firstcall.intake.store import accept
from tests.board_helpers import Board, FakePushService
from tests.conftest import FakeClock

MONDAY_10AM = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)

# Every table must be in exactly one of these. A table added later and left out fails the
# test, so whoever adds it decides what delete does with it.
DELETED_FOR_LEAD = {
    "identities", "activities", "notes", "contacts", "lead_profiles", "outcomes", "alerts",
    "push_sends", "escalations", "jobs", "inbox_emails", "followups",
}  # fmt: skip
KEPT_SCRUBBED: dict[str, tuple[str, ...]] = {
    "leads": ("name", "phone", "email", "item", "possible_duplicates", "edited_fields", "flags"),
    "messages": ("raw", "extracted", "model_answer", "source_time", "category"),
    # Kept as they are: their payloads hold ids and codes only (see the payload test).
    "lead_events": (),
}
NOT_LEAD_DATA = {
    "orgs", "teams", "agents", "sources", "users", "memberships", "user_sessions",
    "push_subscriptions", "budget_ledger", "heartbeats", "followup_runs",
    # Kept for the cost page; it points at a message row, which delete keeps.
    "model_calls",
}  # fmt: skip


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    clock.current = MONDAY_10AM
    return Board(sessions, clock)


def _email(body: str, message_id: str) -> bytes:
    return (
        "From: Ana Ruiz <ana@buyers.example>\nTo: leads@larkspur.example\n"
        "Subject: Cedar Point Lofts\nDate: Mon, 02 Mar 2026 09:00:00 -0500\n"
        f"Message-ID: {message_id}\n\n{body}\n"
    ).encode()


def _count(board: Board, model: Any, *where: Any) -> int:
    with board.sessions() as session:
        return session.scalar(select(func.count()).select_from(model).where(*where)) or 0


def test_every_table_is_classified() -> None:
    classified = DELETED_FOR_LEAD | set(KEPT_SCRUBBED) | NOT_LEAD_DATA
    assert set(Base.metadata.tables) == classified, (
        f"unclassified: {set(Base.metadata.tables) - classified}; "
        f"gone: {classified - set(Base.metadata.tables)}"
    )
    assert not DELETED_FOR_LEAD & NOT_LEAD_DATA


def test_delete_lead_removes_the_buyer_from_every_table(board: Board) -> None:
    push = FakePushService()
    try:
        _check_delete(board, push)
    finally:
        push.close()


def _check_delete(board: Board, push: FakePushService) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("lead@larkspur.example", teams={"north": "team_lead"})
    admin = board.user("admin@larkspur.example", roles=["admin"])
    board.subscribe(seller, push.device())
    first_id = f"<{uuid.uuid4().hex}@mail.example>"
    first = board.pipe.deliver("inbox", _email("Is the loft still free?", first_id))
    lead = first.lead_id
    assert lead is not None
    other = board.lead("north")
    board.set_lead(other, possible_duplicates=[str(lead)])
    board.set_lead(lead, arrival_at=MONDAY_10AM, pool_since=MONDAY_10AM)
    board.clock.current = MONDAY_10AM + timedelta(minutes=20)
    escalate(board.sessions, board.clock)
    client = board.signed_in(seller.email)
    assert client.post(f"/api/leads/{lead}/claim").status_code == 200
    repeat = board.pipe.deliver("inbox", _email("Can I see it Friday?", f"<{uuid.uuid4().hex}@m>"))
    assert repeat.lead_id == lead
    note = {"text": "Called Ana about Friday.", "action": "call"}
    assert client.post(f"/api/leads/{lead}/notes", json=note).status_code == 201
    assert client.put(f"/api/leads/{lead}/profile", json={"need": "garage"}).status_code == 200
    board.clock.current = MONDAY_10AM + timedelta(days=1)
    run_nightly(board.sessions, board.clock)
    assert _count(board, Followup, Followup.lead_id == lead) == 1
    body = {"outcome": "lost", "reason": "timing"}
    assert client.post(f"/api/leads/{lead}/outcome", json=body).status_code == 200
    with board.sessions.begin() as session:
        org = session.get_one(Lead, lead).org_id
        session.add(
            InboxEmail(
                org_id=org, user_id=seller.id, subject="Ana", text_body="Ana", html_body="Ana",
                links=[{"label": "Ana", "path": f"/leads/{lead}"},
                       {"label": "Bo", "path": f"/leads/{other}"}],
                created_at=MONDAY_10AM,
            )
        )  # fmt: skip
        session.add(
            ModelCall(org_id=org, message_id=first.id, model="m", mode="replay",
                      cost_usd=Decimal("0.0002"), ok=True)
        )  # fmt: skip
    message_ids = [first.id, repeat.id]
    with board.sessions() as session:
        alert_ids = list(session.scalars(select(Alert.id).where(Alert.lead_id == lead)))
    for model, column in (
        (Identity, Identity.lead_id), (Activity, Activity.lead_id), (Note, Note.lead_id),
        (Contact, Contact.lead_id), (LeadProfile, LeadProfile.lead_id),
        (Outcome, Outcome.lead_id), (Alert, Alert.lead_id), (Escalation, Escalation.lead_id),
    ):  # fmt: skip
        assert _count(board, model, column == lead) > 0, model
    assert _count(board, PushSend, PushSend.alert_id.in_(alert_ids)) > 0
    assert _count(board, Job, Job.message_id.in_(message_ids)) > 0
    events_before = board.pipe.events(lead)

    for role_user in ("seller@larkspur.example", "lead@larkspur.example"):
        assert board.signed_in(role_user).delete(f"/api/leads/{lead}").status_code == 404
    admin_client = board.signed_in(admin.email)
    assert admin_client.delete(f"/api/leads/{lead}").status_code == 204

    for model, column in (
        (Identity, Identity.lead_id), (Activity, Activity.lead_id), (Note, Note.lead_id),
        (Contact, Contact.lead_id), (LeadProfile, LeadProfile.lead_id),
        (Outcome, Outcome.lead_id), (Alert, Alert.lead_id), (Escalation, Escalation.lead_id),
    ):  # fmt: skip
        assert _count(board, model, column == lead) == 0, model
    assert _count(board, PushSend, PushSend.alert_id.in_(alert_ids)) == 0
    assert _count(board, Job, Job.message_id.in_(message_ids)) == 0
    assert _count(board, InboxEmail) == 0
    assert _count(board, Followup, Followup.lead_id == lead) == 0

    row = board.get(lead)
    for name in KEPT_SCRUBBED["leads"]:
        assert getattr(row, name) in (None, []), name
    assert row.deleted_at == board.clock.now()
    for message_id in message_ids:
        message = board.message(message_id)
        assert (message.raw, message.extracted, message.model_answer) == (b"", None, None)
        assert (message.source_time, message.category) == (None, None)
        assert message.delivery_key is not None
    assert board.get(other).possible_duplicates == []
    assert _count(board, ModelCall, ModelCall.message_id == first.id) == 1

    events = board.pipe.events(lead)
    assert [e.id for e in events[:-1]] == [e.id for e in events_before]
    assert (events[-1].type, events[-1].seq, events[-1].payload) == (
        "lead.deleted",
        events_before[-1].seq + 1,
        {"by": str(admin.id)},
    )

    assert admin_client.get(f"/api/leads/{lead}").status_code == 404
    assert admin_client.delete(f"/api/leads/{lead}").status_code == 404
    assert admin_client.post(f"/api/leads/{lead}/reopen").status_code == 404
    north = board.teams["north"]
    for view in ("pool", "team"):
        listed = admin_client.get(f"/api/leads?view={view}&team_id={north}").json()
        assert str(lead) not in {r["id"] for r in listed}

    source = board.pipe.source("inbox")
    retry = accept(board.sessions, source, _email("Is the loft still free?", first_id),
                   "message/rfc822", {})  # fmt: skip
    assert retry.status == "duplicate_delivery"
    fresh = board.pipe.deliver("inbox", _email("Hello again", f"<{uuid.uuid4().hex}@m>"))
    assert fresh.status == "lead_created"
    assert fresh.lead_id != lead


def test_a_monitor_and_a_team_lead_cannot_delete(board: Board) -> None:
    board.user("monitor@larkspur.example", roles=["monitor"])
    board.user("lead@larkspur.example", teams={"north": "team_lead"})
    lead = board.lead("north")
    for email in ("monitor@larkspur.example", "lead@larkspur.example"):
        assert board.signed_in(email).delete(f"/api/leads/{lead}").status_code == 404
    assert board.get(lead).deleted_at is None
    assert _count(board, Message, Message.lead_id == lead) == 1
