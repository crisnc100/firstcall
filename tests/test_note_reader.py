"""The note reader (F31): a note job reads each note through the gateway into a tidied note,
profile updates and suggested contacts. A suggestion logs nothing until a person confirms
it. With no model the note stays and says so, and a reread reads it later."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import Contact, Job, Lead, LeadProfile, ModelCall, Note, NoteSuggestion, User
from firstcall.model.prompts import READ_NOTE, SYSTEM_PROMPTS
from firstcall.model.transport import TransportRequest
from firstcall.notes.reader import NoteReader, reread_notes
from firstcall.pipeline.jobs import EXHAUSTED, claim, reap, run_one
from firstcall.pipeline.model_read import ModelReader
from tests.board_helpers import Board
from tests.conftest import FakeClock
from tests.model_fakes import FakeTransport, gateway

MONDAY_10AM = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)  # 10:00 in New York
NOTE = "Called Ana this morning. Budget is around $450k. Wants three bedrooms."


def note_answer(**fields: Any) -> str:
    base: dict[str, Any] = {
        "clean_note": "Called Ana. Budget about $450k; wants three bedrooms.",
        "budget": {"text": "around $450k", "min": 450000, "max": 450000},
        "authority": {"value": "unknown", "others": None},
        "need": "three bedrooms",
        "timeline": None,
        "notes": None,
        "actions": [{"kind": "call", "quote": "Called Ana this morning"}],
    }
    return json.dumps({**base, **fields})


def _note_text(request: TransportRequest) -> str:
    found = re.search(r"<note>\n(.*)\n</note>", request.user, re.DOTALL)
    assert found is not None
    return found.group(1)


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    clock.current = MONDAY_10AM
    return Board(sessions, clock)


def _owned(board: Board, owner: User) -> uuid.UUID:
    lead = board.lead("north")
    board.own(lead, owner, MONDAY_10AM)
    board.set_lead(lead, arrival_at=MONDAY_10AM, owned_since=MONDAY_10AM,
                   uncontacted_since=MONDAY_10AM)  # fmt: skip
    return lead


def _write(
    board: Board, email: str, lead: uuid.UUID, text: str = NOTE, action: str | None = None
) -> uuid.UUID:
    answer = board.signed_in(email).post(
        f"/api/leads/{lead}/notes", json={"text": text, "action": action}
    )
    assert answer.status_code == 201, answer.text
    return uuid.UUID(answer.json()["note_id"])


def _reader(board: Board, transport: FakeTransport, **overrides: Any) -> ModelReader:
    return ModelReader(gateway(board.sessions, board.clock, transport, **overrides))


def _work(board: Board, reader: ModelReader | None) -> None:
    while run_one(board.sessions, board.clock, reader=reader):
        pass


def _note(board: Board, note_id: uuid.UUID) -> Note:
    with board.sessions() as session:
        return session.get_one(Note, note_id)


def _suggestions(board: Board, note_id: uuid.UUID) -> list[NoteSuggestion]:
    with board.sessions() as session:
        return list(
            session.scalars(
                select(NoteSuggestion)
                .where(NoteSuggestion.note_id == note_id)
                .order_by(NoteSuggestion.kind)
            )
        )


def _contacts(board: Board, lead: uuid.UUID) -> list[Contact]:
    with board.sessions() as session:
        return list(
            session.scalars(select(Contact).where(Contact.lead_id == lead).order_by(Contact.at))
        )


def _profile(board: Board, lead: uuid.UUID) -> LeadProfile:
    with board.sessions() as session:
        return session.scalars(select(LeadProfile).where(LeadProfile.lead_id == lead)).one()


def _act(client: httpx.Client, lead: uuid.UUID, suggestion: NoteSuggestion, verb: str) -> int:
    return client.post(f"/api/leads/{lead}/suggestions/{suggestion.id}/{verb}").status_code


def test_a_note_is_read_into_a_tidied_note_profile_and_a_suggestion_that_logs_nothing(
    board: Board,
) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    note_id = _write(board, seller.email, lead)
    with board.sessions() as session:
        jobs = session.scalars(select(Job).where(Job.note_id == note_id)).all()
    assert [(j.kind, j.state, j.message_id) for j in jobs] == [("read_note", "queued", None)]
    assert _note(board, note_id).read_state == "pending"

    transport = FakeTransport(default=note_answer())
    _work(board, _reader(board, transport))
    [sent] = transport.requests
    assert sent.system == SYSTEM_PROMPTS[READ_NOTE]
    assert _note_text(sent) == NOTE
    assert "Note date: 2026-03-02" in sent.user
    assert "America/New_York" in sent.user

    note = _note(board, note_id)
    assert (note.read_state, note.read_reason) == ("read", None)
    assert note.clean_note == "Called Ana. Budget about $450k; wants three bedrooms."
    assert [(s.kind, s.quote, s.state) for s in _suggestions(board, note_id)] == [
        ("call", "Called Ana this morning", "open")
    ]
    assert _contacts(board, lead) == []
    assert board.get(lead).first_contact_at is None
    profile = _profile(board, lead)
    assert (profile.budget_min, profile.budget_max, profile.need) == (450000, 450000,
                                                                      "three bedrooms")  # fmt: skip
    with board.sessions() as session:
        logged = session.scalars(select(ModelCall.note_id)).all()
    assert logged == [note_id]

    detail = board.signed_in(seller.email).get(f"/api/leads/{lead}").json()
    [shown] = detail["notes"]
    assert shown["read_state"] == "read"
    assert shown["clean_note"] == note.clean_note
    assert [(s["kind"], s["state"]) for s in shown["suggestions"]] == [("call", "open")]


def test_confirm_logs_one_contact_from_the_note_and_dismiss_logs_none(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    note_id = _write(board, seller.email, lead)
    two = [{"kind": "call", "quote": "Called Ana"}, {"kind": "email", "quote": "Budget is"}]
    _work(board, _reader(board, FakeTransport(default=note_answer(actions=two))))
    call, email = _suggestions(board, note_id)
    client = board.signed_in(seller.email)

    board.clock.advance(hours=2)
    assert _act(client, lead, call, "confirm") == 200
    assert _act(client, lead, call, "confirm") == 409
    assert _act(client, lead, email, "dismiss") == 200
    assert _act(client, lead, email, "confirm") == 409
    [contact] = _contacts(board, lead)
    assert (contact.kind, contact.source, contact.note_id) == ("call", "suggestion", note_id)
    assert contact.at == MONDAY_10AM
    assert (contact.made_by, contact.logged_by) == (seller.id, seller.id)
    assert [s.state for s in _suggestions(board, note_id)] == ["confirmed", "dismissed"]
    assert board.get(lead).first_contact_at == MONDAY_10AM


def test_a_kind_the_note_already_logged_is_closed_as_already_logged(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    note_id = _write(board, seller.email, lead, action="call")
    _work(board, _reader(board, FakeTransport(default=note_answer())))
    [suggestion] = _suggestions(board, note_id)
    assert suggestion.state == "already_logged"
    assert len(_contacts(board, lead)) == 1


def test_a_suggestion_whose_quote_is_not_in_the_note_is_dropped(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    note_id = _write(board, seller.email, lead)
    made_up = [{"kind": "meeting", "quote": "Met Ana at the model home"}]
    _work(board, _reader(board, FakeTransport(default=note_answer(actions=made_up))))
    assert _note(board, note_id).read_state == "read"
    assert _suggestions(board, note_id) == []


def test_an_older_note_confirmed_after_a_later_contact_moves_first_contact_back(
    board: Board,
) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    older = _write(board, seller.email, lead)
    _work(board, _reader(board, FakeTransport(default=note_answer())))
    board.clock.advance(hours=3)
    _write(board, seller.email, lead, "Texted her the photos.", "text")
    assert board.get(lead).first_contact_at == MONDAY_10AM + timedelta(hours=3)
    [suggestion] = _suggestions(board, older)
    assert _act(board.signed_in(seller.email), lead, suggestion, "confirm") == 200
    row = board.get(lead)
    assert row.first_contact_at == MONDAY_10AM
    assert row.first_contact_minutes == 0


def test_the_profile_merge_keeps_person_fields_and_the_newest_notes_value(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    client = board.signed_in(seller.email)
    assert client.put(f"/api/leads/{lead}/profile", json={"need": "a garage"}).status_code == 200
    first = _write(board, seller.email, lead, "Budget is around $450k. Wants a pool.")
    board.clock.advance(minutes=5)
    second = _write(board, seller.email, lead, "Budget is around $500k. Likes the yard.")

    def respond(request: TransportRequest) -> str:
        if "$500k" in _note_text(request):
            return note_answer(budget={"text": "around $500k", "min": 500000, "max": 500000},
                               need="a yard", notes="Likes the yard.", actions=[])  # fmt: skip
        return note_answer(budget={"text": "around $450k", "min": 450000, "max": 450000},
                           need="a pool", notes="Wants a pool.", actions=[],
                           timeline={"text": "by June", "month": "2026-06"})  # fmt: skip

    transport = FakeTransport(respond=respond)
    reader = _reader(board, transport)
    # The newer note is read first; the older one, read later, cannot replace its values.
    later = MONDAY_10AM + timedelta(days=1)
    with board.sessions.begin() as session:
        session.execute(update(Job).where(Job.note_id == first).values(run_after=later))
    _work(board, reader)
    board.clock.advance(days=2)
    _work(board, reader)
    assert {_note(board, n).read_state for n in (first, second)} == {"read"}

    profile = _profile(board, lead)
    assert profile.need == "a garage"
    assert (profile.budget_text, profile.budget_min) == ("around $500k", 500000)
    # The older note still fills what the newer one left empty.
    assert profile.timeline_month == "2026-06"
    assert profile.notes is not None
    assert profile.notes.splitlines() == ["Likes the yard.", "Wants a pool."]

    # An empty model value never clears a field.
    third = _write(board, seller.email, lead, "Left a voicemail.")
    empty = note_answer(budget=None, need=None, timeline=None, actions=[])
    _work(board, _reader(board, FakeTransport(default=empty)))
    assert _note(board, third).read_state == "read"
    assert _profile(board, lead).budget_min == 500000


def test_with_no_key_the_note_is_saved_and_says_it_needs_a_model_key(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    note_id = _write(board, seller.email, lead)
    transport = FakeTransport(default=note_answer())
    _work(board, _reader(board, transport, has_key=False))
    assert transport.requests == []
    note = _note(board, note_id)
    assert (note.read_state, note.read_reason) == ("needs_model", "no_key")
    detail = board.signed_in(seller.email).get(f"/api/leads/{lead}")
    assert detail.status_code == 200
    [shown] = detail.json()["notes"]
    assert (shown["read_state"], shown["read_reason"], shown["suggestions"]) == (
        "needs_model", "no_key", [],
    )  # fmt: skip

    off = NoteReader(_reader(board, transport, has_key=False).gateway)
    assert reread_notes(board.sessions, off) == {"halted": 1}
    assert reread_notes(board.sessions, NoteReader(_reader(board, transport).gateway)) == {
        "read": 1
    }
    assert _note(board, note_id).read_state == "read"
    assert [s.kind for s in _suggestions(board, note_id)] == ["call"]


def test_an_unreadable_answer_fails_the_note(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    note_id = _write(board, seller.email, lead)
    _work(board, _reader(board, FakeTransport(default="not json")))
    note = _note(board, note_id)
    assert (note.read_state, note.read_reason) == ("failed", "unreadable")


def test_a_reaped_note_job_runs_again_and_an_exhausted_one_fails_its_note(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    note_id = _write(board, seller.email, lead)
    claimed = claim(board.sessions, board.clock, 60, 5)
    assert claimed is not None
    assert (claimed.note_id, claimed.message_id) == (note_id, None)
    board.clock.advance(minutes=5)
    assert reap(board.sessions, board.clock) == 1
    _work(board, _reader(board, FakeTransport(default=note_answer())))
    assert _note(board, note_id).read_state == "read"

    other = _write(board, seller.email, lead, "Texted Ana the price sheet.")
    with board.sessions.begin() as session:
        session.execute(update(Job).where(Job.note_id == other).values(attempts=5))
    assert run_one(board.sessions, board.clock) is False
    note = _note(board, other)
    assert (note.read_state, note.read_reason) == ("failed", EXHAUSTED)
    assert note.text == "Texted Ana the price sheet."


def test_who_may_confirm_and_when(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("other@larkspur.example", teams={"north": "salesperson"})
    board.user("south@larkspur.example", teams={"south": "salesperson"})
    board.user("lead@larkspur.example", teams={"north": "team_lead"})
    board.user("monitor@larkspur.example", roles=["monitor"])
    newcomer = board.user("new@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    note_id = _write(board, seller.email, lead)
    two = [{"kind": "call", "quote": "Called Ana"}, {"kind": "text", "quote": "Budget is"}]
    _work(board, _reader(board, FakeTransport(default=note_answer(actions=two))))
    call, text = _suggestions(board, note_id)

    for email in ("other@larkspur.example", "south@larkspur.example",
                  "monitor@larkspur.example"):  # fmt: skip
        client = board.signed_in(email)
        assert _act(client, lead, call, "confirm") == 404
        assert _act(client, lead, call, "dismiss") == 404
    team_lead = board.signed_in("lead@larkspur.example")
    assert _act(team_lead, lead, call, "dismiss") == 200

    # A closed lead's suggestion waits until the lead is reopened.
    body = {"outcome": "lost", "reason": "timing"}
    assert team_lead.post(f"/api/leads/{lead}/outcome", json=body).status_code == 200
    assert _act(team_lead, lead, text, "confirm") == 409
    assert team_lead.post(f"/api/leads/{lead}/reopen").status_code == 200

    # After a reassign, the old owner's open suggestion is the new owner's to confirm.
    move = {"user_id": str(newcomer.id)}
    assert team_lead.post(f"/api/leads/{lead}/reassign", json=move).status_code == 200
    assert _act(board.signed_in(newcomer.email), lead, text, "confirm") == 200
    contacts = _contacts(board, lead)
    assert [(c.kind, c.made_by, c.logged_by) for c in contacts if c.kind == "text"] == [
        ("text", seller.id, newcomer.id)
    ]
    with board.sessions() as session:
        assert session.get_one(Lead, lead).owner_id == newcomer.id


def test_delete_removes_suggestions_and_note_jobs_and_keeps_the_calls(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("admin@larkspur.example", roles=["admin"])
    lead = _owned(board, seller)
    read = _write(board, seller.email, lead)
    _work(board, _reader(board, FakeTransport(default=note_answer())))
    waiting = _write(board, seller.email, lead, "Texted Ana.")
    assert board.signed_in("admin@larkspur.example").delete(f"/api/leads/{lead}").status_code == 204
    with board.sessions() as session:
        assert session.scalars(select(NoteSuggestion)).all() == []
        assert session.scalars(select(Job).where(Job.note_id.in_([read, waiting]))).all() == []
        assert session.scalars(select(Note)).all() == []
        assert [c.note_id for c in session.scalars(select(ModelCall))] == [None]


def test_an_offline_worker_leaves_the_note_for_the_reread_pass(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = _owned(board, seller)
    note_id = _write(board, seller.email, lead)
    _work(board, None)
    note = _note(board, note_id)
    assert (note.read_state, note.read_reason) == ("needs_model", "offline")


def test_an_unassigned_lead_or_unreadable_hours_dates_the_note_in_utc(board: Board) -> None:
    from firstcall.db import Team

    board.user("admin@larkspur.example", roles=["admin"])
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    # Both leads first: delivering a lead runs every queued job, notes included.
    unassigned = board.lead(None)
    lead = _owned(board, seller)
    _write(board, "admin@larkspur.example", unassigned, "Spoke about Friday.")
    _write(board, seller.email, lead)
    with board.sessions.begin() as session:
        session.execute(update(Team).where(Team.slug == "north").values(timezone="Mars/Base"))
    transport = FakeTransport(default=note_answer(actions=[]))
    _work(board, _reader(board, transport))
    assert [r.user.splitlines()[1] for r in transport.requests] == ["Time zone: UTC"] * 2


def test_the_reread_pass_skips_a_note_read_or_deleted_meanwhile(board: Board) -> None:
    from firstcall.notes.reader import read_note_job

    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("admin@larkspur.example", roles=["admin"])
    lead = _owned(board, seller)
    note_id = _write(board, seller.email, lead)
    _work(board, None)
    transport = FakeTransport(default=note_answer())
    reader = NoteReader(_reader(board, transport).gateway)
    # A note no longer waiting is left alone.
    with board.sessions.begin() as session:
        assert read_note_job(session, note_id, board.clock, reader) == "skipped"
    assert transport.requests == []
    # The lead is deleted while the model reads: nothing reaches the tombstone.
    real = reader.read

    def read_then_delete(*args: Any) -> Any:
        found = real(*args)
        admin = board.signed_in("admin@larkspur.example")
        assert admin.delete(f"/api/leads/{lead}").status_code == 204
        return found

    reader.read = read_then_delete  # type: ignore[method-assign]
    assert reread_notes(board.sessions, reader) == {}
    assert board.get(lead).deleted_at is not None
    with board.sessions() as session:
        assert session.scalars(select(NoteSuggestion)).all() == []
