"""The fixes from the PR3a phases 1-3 review: a team's cadence survives the seed, a deleted
lead's messages leave the reread queue, an earlier message moves time to first contact, My
leads puts due follow-ups first before its limit, no entry point changes a deleted lead, a
closed day keeps an earlier rhythm step, the scorer scales a number only by a magnitude the
script uses, and the note gold follows what each script says."""

from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import routes
from firstcall.board.escalate import escalate
from firstcall.board.followups import run_nightly
from firstcall.core.cadence import Cadence, Step, cadence_from, current_step
from firstcall.core.hours import hours_from
from firstcall.corpus.notes import (
    LABELS,
    NOTE_SEEDS,
    SCRIPTS,
    SEEN_STYLES,
    UNSEEN_STYLES,
    read_jsonl,
)
from firstcall.corpus.world import load_world
from firstcall.db import Alert, Followup, Lead, Message, Team
from firstcall.evaluation.note_score import unsupported
from firstcall.seed import seed
from tests.board_helpers import Board
from tests.conftest import FakeClock

MONDAY_10AM = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)  # 10:00 in New York


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    clock.current = MONDAY_10AM
    return Board(sessions, clock)


def test_the_seed_keeps_each_teams_cadence(sessions: sessionmaker[Session]) -> None:
    world = load_world()
    raw = copy.deepcopy(world.raw)
    raw["teams"][0]["cadence"] = {"steps": [0], "steady": "daily"}
    seed(sessions, type(world)(raw))
    with sessions() as session:
        team = session.scalars(select(Team).where(Team.slug == raw["teams"][0]["slug"])).one()
        other = session.scalars(select(Team).where(Team.slug == raw["teams"][1]["slug"])).one()
    assert team.cadence == {"steps": [0], "steady": "daily"}
    assert other.cadence == {}


def test_the_seed_refuses_a_bad_cadence(sessions: sessionmaker[Session]) -> None:
    world = load_world()
    raw = copy.deepcopy(world.raw)
    raw["teams"][0]["cadence"] = {"steady": "hourly"}
    with pytest.raises(ValueError, match="rhythm"):
        seed(sessions, type(world)(raw))


def test_deleting_a_lead_takes_its_messages_out_of_the_reread_queue(board: Board) -> None:
    board.user("admin@larkspur.example", roles=["admin"])
    lead = board.lead("north")
    with board.sessions.begin() as session:
        session.execute(update(Message).where(Message.lead_id == lead).values(needs_reread=True))
    admin = board.signed_in("admin@larkspur.example")
    assert admin.delete(f"/api/leads/{lead}").status_code == 204
    with board.sessions() as session:
        flags = list(session.scalars(select(Message.needs_reread).where(Message.lead_id == lead)))
    assert flags
    assert not any(flags)


def test_an_earlier_message_moves_the_time_to_first_contact(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    phone, email = "202-555-0142", f"{uuid.uuid4().hex[:10]}@buyers.example"
    lead = board.lead("north", phone=phone, email=email)
    board.own(lead, seller, MONDAY_10AM)
    board.set_lead(lead, arrival_at=MONDAY_10AM)
    board.clock.current = MONDAY_10AM + timedelta(minutes=10)
    client = board.signed_in(seller.email)
    note = {"text": "Called about the loft.", "action": "call"}
    assert client.post(f"/api/leads/{lead}/notes", json=note).status_code == 201
    row = board.get(lead)
    assert (row.first_contact_minutes, row.first_contact_business_minutes) == (10, 10)

    payload = {"name": "Ana Ruiz", "phone": phone, "email": email, "message": "Sent earlier."}
    message_id = board.pipe.store("site-form", json.dumps(payload).encode())
    with board.sessions.begin() as session:
        session.execute(
            update(Message)
            .where(Message.id == message_id)
            .values(received_at=MONDAY_10AM - timedelta(hours=1))
        )
    board.pipe.run()
    assert board.message(message_id).lead_id == lead
    row = board.get(lead)
    assert row.arrival_at == MONDAY_10AM - timedelta(hours=1)
    assert row.first_contact_at == MONDAY_10AM + timedelta(minutes=10)
    assert row.first_contact_minutes == Decimal("70")
    assert row.first_contact_business_minutes == Decimal("70")


def test_my_leads_puts_due_follow_ups_first_before_the_limit(
    board: Board, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "LIST_LIMIT", 2)
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    made = []
    for hours in (3, 2, 1):
        lead = board.lead("north")
        board.own(lead, seller)
        board.set_lead(lead, arrival_at=MONDAY_10AM - timedelta(hours=hours))
        made.append(lead)
    newest = made[-1]
    with board.sessions.begin() as session:
        org = session.get_one(Lead, newest).org_id
        session.add(
            Followup(id=uuid.uuid4(), org_id=org, lead_id=newest, owner_id=seller.id,
                     due_on=date(2026, 2, 27), step="day 0", next_step="Call.", state="open",
                     created_at=MONDAY_10AM)
        )  # fmt: skip
    listed = board.signed_in(seller.email).get("/api/leads?view=mine").json()
    assert [row["id"] for row in listed] == [str(newest), str(made[0])]


def test_no_entry_point_changes_a_deleted_lead(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    other = board.user("other@larkspur.example", teams={"north": "salesperson"})
    board.user("admin@larkspur.example", roles=["admin"])
    owned, nurtured = board.lead("north"), board.lead("north")
    board.own(owned, seller, MONDAY_10AM)
    board.own(nurtured, seller, MONDAY_10AM)
    board.set_lead(nurtured, state="nurture")
    admin = board.signed_in("admin@larkspur.example")
    for lead in (owned, nurtured):
        assert admin.delete(f"/api/leads/{lead}").status_code == 204
    before = {lead: board.pipe.events(lead) for lead in (owned, nurtured)}
    states = {lead: (board.get(lead).state, board.get(lead).owner_id) for lead in before}
    assert {s for s, _ in states.values()} == {"owned", "nurture"}

    north, south = board.teams["north"], board.teams["south"]
    for lead in before:
        for method, path, body in (
            ("post", "claim", None), ("post", "release", None), ("post", "return", None),
            ("post", "reassign", {"user_id": str(other.id)}),
            ("post", "move", {"team_id": str(south)}), ("post", "place", {"team_id": str(north)}),
            ("post", "notes", {"text": "Called.", "action": "call"}),
            ("put", "profile", {"need": "garage"}),
            ("post", "outcome", {"outcome": "won"}), ("post", "nurture", None),
            ("post", "resume", None), ("post", "reopen", None), ("delete", "", None),
        ):  # fmt: skip
            url = f"/api/leads/{lead}/{path}".rstrip("/")
            answer = admin.request(method, url, json=body)
            assert answer.status_code == 404, (path, answer.status_code)

    board.clock.current = MONDAY_10AM + timedelta(days=2)
    escalate(board.sessions, board.clock)
    run_nightly(board.sessions, board.clock)
    assert admin.patch(f"/api/users/{other.id}", json={"teams": {}}).status_code == 200
    assert admin.patch(f"/api/users/{seller.id}", json={"teams": {}}).status_code == 200
    assert admin.patch(f"/api/users/{seller.id}", json={"active": False}).status_code == 200

    for lead, events in before.items():
        assert [e.id for e in board.pipe.events(lead)] == [e.id for e in events]
        assert (board.get(lead).state, board.get(lead).owner_id) == states[lead]
        with board.sessions() as session:
            assert session.scalars(select(Alert.id).where(Alert.lead_id == lead)).all() == []
            assert session.scalars(select(Followup.id).where(Followup.lead_id == lead)).all() == []


WEEKDAYS = hours_from(
    {"days": ["mon", "tue", "wed", "thu", "fri"], "open": "09:00", "close": "17:00"},
    "America/New_York",
)


def test_a_closed_day_keeps_the_earlier_rhythm_step() -> None:
    since = date(2026, 3, 11)  # a Wednesday; the second monthly step, 10 May, is a Sunday
    may_11 = date(2026, 5, 11)
    for today, due_on in ((date(2026, 5, 10), date(2026, 4, 10)), (may_11, may_11)):
        found = current_step("nurture", since, today, None, since, Cadence(), WEEKDAYS)
        assert found == Step(due_on, "monthly")


def test_a_closed_day_keeps_the_earlier_steady_step_of_an_owned_lead() -> None:
    # Weekly from Saturday 7 March: the steps fall on Saturdays and move to Mondays. On
    # Sunday 22 March the step of Monday 16 March stands, not day 0.
    plan = cadence_from({"steps": [0], "steady": "weekly"})
    saturday = date(2026, 3, 7)
    assert current_step("owned", saturday, date(2026, 3, 22), None, None, plan, WEEKDAYS) == (
        Step(date(2026, 3, 16), "weekly")
    )


def test_a_magnitude_the_script_never_used_is_unsupported() -> None:
    assert unsupported("Has two dogs.", answer(notes="Budget is two million.")) == [
        "notes: number 2E+6"
    ]
    assert unsupported("Has 2 dogs.", answer(notes="Budget is $2k.")) == ["notes: number 2E+3"]
    assert unsupported("Between 380 and 420 thousand.", answer(notes="$380,000")) == []
    assert unsupported("Budget is 1.2 million, 3 kids.", answer(notes="1.2M budget")) == []


def answer(**fields: object) -> dict[str, object]:
    return {"clean_note": "", "budget": None, "authority": None, "need": None,
            "timeline": None, "notes": "", **fields}  # fmt: skip


# What each number style looks like in a script.
STYLE_TEXT = {
    "k": r"\$\d+k\b",
    "upper_k": r"(?<!\$)\b\d+K\b",
    "comma": r"\$\d{1,3}(,\d{3})+",
    "thousand": r"(?<!\$)\b\d+ thousand\b",
    "words": r"\b(hundred|thousand)\b",
    "grand": r"\b\d+ grand\b",
    "million": r"\$\d+(\.\d+)? million\b",
    "short_m": r"\$\d+(\.\d+)?M\b",
}


@pytest.mark.parametrize("seed", NOTE_SEEDS)
def test_each_recorded_number_style_is_the_one_the_script_shows(seed: int) -> None:
    assert set(STYLE_TEXT) == set(SEEN_STYLES) | set(UNSEEN_STYLES)
    problems = []
    for script, row in zip(read_jsonl(seed, SCRIPTS), read_jsonl(seed, LABELS), strict=True):
        styles = [p.removeprefix("style-") for p in row["phrasings"] if p.startswith("style-")]
        for style in styles:
            if not re.search(STYLE_TEXT[style], script["text"]):
                problems.append(f"{row['id']} {style}")
        if any(s in UNSEEN_STYLES for s in styles) and row["seen"]:
            problems.append(f"{row['id']} seen with an unseen style")
    assert problems == []


@pytest.mark.parametrize("seed", NOTE_SEEDS)
def test_a_stated_corner_lot_is_a_need_in_the_gold(seed: int) -> None:
    problems = []
    for script, row in zip(read_jsonl(seed, SCRIPTS), read_jsonl(seed, LABELS), strict=True):
        stated = "corner lot" in script["text"]
        if stated and (row["need"] is not True or "corner lot" not in row["terms"]["need"]):
            problems.append(row["id"])
    assert problems == []
