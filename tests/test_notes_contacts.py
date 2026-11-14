"""Typed notes, real contacts and time to first contact, the profile, and the contact-due
escalation (F30)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import notes
from firstcall.board.escalate import escalate
from firstcall.db import Alert, Contact, Lead, Note, User
from tests.board_helpers import Board
from tests.conftest import FakeClock

# Monday 2 March 2026, 10:00 in New York (North's time zone), inside business hours.
MONDAY_10AM = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    clock.current = MONDAY_10AM
    return Board(sessions, clock)


def _claimed(board: Board, seller: User) -> uuid.UUID:
    """A North lead that arrived now on the test clock, claimed by the seller."""
    lead = board.lead("north")
    now = board.clock.now()
    board.set_lead(lead, arrival_at=now, pool_since=now)
    client = board.signed_in(seller.email)
    assert client.post(f"/api/leads/{lead}/claim").status_code == 200
    return lead


def _note(
    board: Board, email: str, lead: uuid.UUID, text: str, action: str | None = None
) -> httpx.Response:
    return board.signed_in(email).post(
        f"/api/leads/{lead}/notes", json={"text": text, "action": action}
    )


def _contacts(board: Board, lead: uuid.UUID) -> list[Contact]:
    with board.sessions() as session:
        return list(session.scalars(select(Contact).where(Contact.lead_id == lead)))


def test_a_note_with_an_action_logs_one_contact_and_sets_first_contact(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _claimed(board, seller)
    board.clock.advance(minutes=30)
    answer = _note(board, seller.email, lead, "Called her about the loft.", "call")
    assert answer.status_code == 201, answer.text
    assert answer.json()["contact_id"] is not None
    contacts = _contacts(board, lead)
    assert [(c.kind, c.source, c.made_by) for c in contacts] == [("call", "typed", seller.id)]
    row = board.get(lead)
    assert row.first_contact_at == MONDAY_10AM + timedelta(minutes=30)
    assert row.first_contact_minutes == Decimal("30.00")
    assert row.first_contact_business_minutes == Decimal("30.00")
    assert row.uncontacted_since is None
    types = [e.type for e in board.pipe.events(lead)]
    assert types[-2:] == ["note.added", "contact.logged"]


def test_a_note_without_an_action_is_not_a_touch(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _claimed(board, seller)
    assert _note(board, seller.email, lead, "Will call tomorrow.").status_code == 201
    assert _contacts(board, lead) == []
    row = board.get(lead)
    assert row.first_contact_at is None
    assert row.uncontacted_since == MONDAY_10AM
    assert [e.type for e in board.pipe.events(lead)][-1] == "note.added"


def test_first_contact_counts_business_minutes_over_a_night(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _claimed(board, seller)
    # 10:00 Monday to 10:00 Tuesday is 24 hours of wall-clock and 9 business hours.
    board.clock.current = MONDAY_10AM + timedelta(days=1)
    _note(board, seller.email, lead, "Texted the floor plans.", "text")
    row = board.get(lead)
    assert row.first_contact_minutes == Decimal("1440.00")
    assert row.first_contact_business_minutes == Decimal("540.00")


def test_an_earlier_contact_replaces_the_first_contact(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _claimed(board, seller)
    board.clock.advance(minutes=90)
    _note(board, seller.email, lead, "Met at the loft.", "meeting")
    with board.sessions.begin() as session:
        row = session.get_one(Lead, lead, with_for_update=True)
        note = session.scalars(select(Note).where(Note.lead_id == lead)).one()
        earlier = MONDAY_10AM + timedelta(minutes=20)
        logged = notes.log_contact(session, row, note, "call", seller.id, seller.id,
                                   "suggestion", earlier)  # fmt: skip
        assert logged is not None
    row = board.get(lead)
    assert row.first_contact_at == MONDAY_10AM + timedelta(minutes=20)
    assert row.first_contact_minutes == Decimal("20.00")


def test_one_note_logs_one_contact_per_kind(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _claimed(board, seller)
    _note(board, seller.email, lead, "Called her.", "call")
    with board.sessions.begin() as session:
        row = session.get_one(Lead, lead, with_for_update=True)
        note = session.scalars(select(Note).where(Note.lead_id == lead)).one()
        again = notes.log_contact(session, row, note, "call", seller.id, seller.id,
                                  "suggestion", board.clock.now())  # fmt: skip
        assert again is None
    assert len(_contacts(board, lead)) == 1
    assert [e.type for e in board.pipe.events(lead)].count("contact.logged") == 1


def test_a_team_lead_logs_a_contact_on_a_lead_in_their_team(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
    lead = _claimed(board, seller)
    assert _note(board, lead_user.email, lead, "Called her myself.", "call").status_code == 201
    assert [c.made_by for c in _contacts(board, lead)] == [lead_user.id]


def test_a_contact_needs_an_owned_or_nurture_lead(board: Board) -> None:
    lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
    lead = board.lead("north")
    answer = _note(board, lead_user.email, lead, "Called.", "call")
    assert answer.status_code == 422
    assert _note(board, lead_user.email, lead, "Plain note on a pool lead.").status_code == 201
    assert _contacts(board, lead) == []


@pytest.mark.parametrize(
    ("text", "action"), [("", None), ("   ", None), ("x" * 5001, None), ("ok", "fax")]
)
def test_a_bad_note_is_refused(board: Board, text: str, action: str | None) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _claimed(board, seller)
    assert _note(board, seller.email, lead, text, action).status_code == 422


def test_a_person_edit_marks_its_fields(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _claimed(board, seller)
    client = board.signed_in(seller.email)
    answer = client.put(
        f"/api/leads/{lead}/profile",
        json={"budget_min": 400000, "budget_max": 450000, "authority": "shares_decision"},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["person_fields"] == ["authority", "budget_max", "budget_min"]
    answer = client.put(f"/api/leads/{lead}/profile", json={"timeline_month": "2026-07"})
    assert answer.json()["person_fields"] == [
        "authority",
        "budget_max",
        "budget_min",
        "timeline_month",
    ]
    detail = client.get(f"/api/leads/{lead}").json()
    assert detail["profile"]["budget_min"] == 400000
    assert detail["profile"]["authority"] == "shares_decision"


@pytest.mark.parametrize(
    "change",
    [
        {"authority": "boss"},
        {"timeline_month": "2026-13"},
        {"timeline_month": "July"},
        {"budget_min": -5},
        {"budget_max": True},
    ],
)
def test_a_bad_profile_edit_is_refused(board: Board, change: dict[str, object]) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _claimed(board, seller)
    answer = board.signed_in(seller.email).put(f"/api/leads/{lead}/profile", json=change)
    assert answer.status_code == 422


# --- the contact-due escalation ---------------------------------------------------------


def _fired(board: Board, at: datetime) -> set[tuple[uuid.UUID, str, uuid.UUID]]:
    board.clock.current = at
    with board.sessions() as session:
        before = set(session.scalars(select(Alert.id)))
    escalate(board.sessions, board.clock)
    with board.sessions() as session:
        new = session.scalars(select(Alert).where(Alert.id.not_in(before)))
        return {
            (a.lead_id, a.kind, a.user_id) for a in new if a.kind.startswith("escalation:contact")
        }


OWNER = "escalation:contact_owner"
LEADS = "escalation:contact_team_leads"


def test_contact_due_fires_at_240_then_300_business_minutes_once(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
    lead = _claimed(board, seller)
    assert _fired(board, MONDAY_10AM + timedelta(minutes=239)) == set()
    assert _fired(board, MONDAY_10AM + timedelta(minutes=240)) == {(lead, OWNER, seller.id)}
    assert _fired(board, MONDAY_10AM + timedelta(minutes=241)) == set()
    assert _fired(board, MONDAY_10AM + timedelta(minutes=299)) == set()
    assert _fired(board, MONDAY_10AM + timedelta(minutes=300)) == {(lead, LEADS, lead_user.id)}
    assert _fired(board, MONDAY_10AM + timedelta(minutes=300)) == set()


def test_contact_due_counts_business_minutes(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.clock.current = datetime(2026, 3, 2, 22, 0, tzinfo=UTC)  # 17:00 New York
    lead = _claimed(board, seller)
    # One business hour on Monday, then three more from Tuesday's 09:00 opening.
    assert _fired(board, datetime(2026, 3, 3, 16, 59, tzinfo=UTC)) == set()
    assert _fired(board, datetime(2026, 3, 3, 17, 0, tzinfo=UTC)) == {(lead, OWNER, seller.id)}


def test_a_contact_stops_the_contact_due_levels(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("lead@larkspur.example", teams={"north": "team_lead"})
    lead = _claimed(board, seller)
    board.clock.current = MONDAY_10AM + timedelta(minutes=100)
    _note(board, seller.email, lead, "Called.", "call")
    assert _fired(board, MONDAY_10AM + timedelta(minutes=400)) == set()


def test_release_and_reclaim_or_reassign_do_not_restart_the_clock(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    other = board.user("other@larkspur.example", teams={"north": "salesperson"})
    lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
    lead = _claimed(board, seller)
    client = board.signed_in(seller.email)
    board.clock.current = MONDAY_10AM + timedelta(minutes=200)
    assert client.post(f"/api/leads/{lead}/release").status_code == 200
    assert client.post(f"/api/leads/{lead}/claim").status_code == 200
    assert board.get(lead).uncontacted_since == MONDAY_10AM
    assert _fired(board, MONDAY_10AM + timedelta(minutes=240)) == {(lead, OWNER, seller.id)}
    reassign = board.signed_in(lead_user.email).post(
        f"/api/leads/{lead}/reassign", json={"user_id": str(other.id)}
    )
    assert reassign.status_code == 200
    assert board.get(lead).uncontacted_since == MONDAY_10AM
    assert _fired(board, MONDAY_10AM + timedelta(minutes=300)) == {(lead, LEADS, lead_user.id)}


def test_nurture_pauses_the_contact_clock(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _claimed(board, seller)
    client = board.signed_in(seller.email)
    assert client.post(f"/api/leads/{lead}/nurture").status_code == 200
    assert _fired(board, MONDAY_10AM + timedelta(minutes=400)) == set()
    assert client.post(f"/api/leads/{lead}/resume").status_code == 200
    assert _fired(board, MONDAY_10AM + timedelta(minutes=401)) == {(lead, OWNER, seller.id)}


def test_a_reopen_starts_a_new_uncontacted_stretch(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
    lead = _claimed(board, seller)
    assert _fired(board, MONDAY_10AM + timedelta(minutes=240)) == {(lead, OWNER, seller.id)}
    client = board.signed_in(seller.email)
    body = {"outcome": "lost", "reason": "timing"}
    assert client.post(f"/api/leads/{lead}/outcome", json=body).status_code == 200
    board.clock.current = MONDAY_10AM + timedelta(minutes=250)
    reopened = board.signed_in(lead_user.email).post(f"/api/leads/{lead}/reopen")
    assert reopened.status_code == 200
    assert board.get(lead).state == "owned"
    assert board.get(lead).uncontacted_since == MONDAY_10AM + timedelta(minutes=250)
    # 230 business minutes to Monday's 18:00 close, then 10 more from Tuesday's 09:00.
    assert _fired(board, datetime(2026, 3, 3, 14, 9, tzinfo=UTC)) == set()
    assert _fired(board, datetime(2026, 3, 3, 14, 10, tzinfo=UTC)) == {(lead, OWNER, seller.id)}


def test_the_escalation_log_marks_a_contact_level_current_until_a_contact(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("admin@larkspur.example", roles=["admin"])
    lead = _claimed(board, seller)
    _fired(board, MONDAY_10AM + timedelta(minutes=240))
    admin = board.signed_in("admin@larkspur.example")
    rows = admin.get("/api/escalations").json()
    assert [(r["level"], r["current"]) for r in rows] == [("contact_owner", True)]
    _note(board, seller.email, lead, "Called.", "call")
    rows = admin.get("/api/escalations").json()
    assert [(r["level"], r["current"]) for r in rows] == [("contact_owner", False)]


def test_contact_due_watches_only_an_owned_lead_with_a_stretch() -> None:
    from firstcall.core.escalation import contact_due, overrides_from
    from firstcall.core.hours import hours_from

    hours = hours_from(
        {"days": ["mon", "tue", "wed", "thu", "fri", "sat"], "open": "09:00", "close": "18:00"},
        "America/New_York",
    )
    later = MONDAY_10AM + timedelta(minutes=400)
    assert contact_due("nurture", MONDAY_10AM, later, hours, frozenset()) == []
    assert contact_due("owned", None, later, hours, frozenset()) == []
    overrides = overrides_from({"contact_owner": 60})
    found = contact_due("owned", MONDAY_10AM, MONDAY_10AM + timedelta(minutes=60), hours,
                        frozenset(), overrides)  # fmt: skip
    assert [level.name for level in found] == ["contact_owner"]
