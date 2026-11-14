"""Who may read and write notes, contacts, the profile and outcomes, reopen and delete
(F35), and what lead event payloads may carry."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import LeadEvent
from tests.board_helpers import Board
from tests.conftest import FakeClock

ROUTES = (
    "read", "note", "contact", "profile", "outcome", "nurture", "resume", "reopen", "delete",
)  # fmt: skip
ROLES = ("salesperson", "team_lead", "monitor", "admin")
WHERE = ("own", "other", "unassigned")
TEAM = {"own": "north", "other": "south", "unassigned": None}
WRITES = {"note": 201, "contact": 201, "profile": 200}
MOVES = {"outcome": 200, "nurture": 200, "resume": 200}

# Everything not listed answers 404 with the body of a lead that does not exist. For "read",
# 200 means the lead detail with its notes, and 0 the lead detail without any.
ALLOWED: dict[tuple[str, str, str], int] = {
    **{("monitor", "read", w): 0 for w in WHERE},
    **{("salesperson", r, "own"): c for r, c in {"read": 200, **WRITES, **MOVES}.items()},
    **{("team_lead", r, "own"): c for r, c in {"read": 200, **WRITES, **MOVES}.items()},
    ("team_lead", "reopen", "own"): 200,
    **{("admin", r, w): c for r, c in {"read": 200, **WRITES}.items() for w in WHERE},
    **{("admin", "reopen", w): 200 for w in ("own", "other")},
    **{("admin", "delete", w): 204 for w in WHERE},
    # An admin may note an Unassigned lead, but it has no owner, so no contact and no reopen.
    ("admin", "contact", "unassigned"): 422,
    ("admin", "reopen", "unassigned"): 409,
}


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    clock.current = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
    return Board(sessions, clock)


class Cast:
    def __init__(self, board: Board) -> None:
        self.actors = {
            "salesperson": board.user("seller@larkspur.example", teams={"north": "salesperson"}),
            "team_lead": board.user("lead@larkspur.example", teams={"north": "team_lead"}),
            "monitor": board.user("monitor@larkspur.example", roles=["monitor"]),
            "admin": board.user("admin@larkspur.example", roles=["admin"]),
        }
        self.owner = {
            "north": board.user("north2@larkspur.example", teams={"north": "salesperson"}),
            "south": board.user("south1@larkspur.example", teams={"south": "salesperson"}),
        }
        self.clients = {role: board.signed_in(user.email) for role, user in self.actors.items()}


STATE_FOR = {"resume": "nurture", "reopen": "won", "delete": "pool"}


def _setup(board: Board, cast: Cast, role: str, route: str, where: str) -> uuid.UUID:
    team = TEAM[where]
    lead = board.lead(team)
    if team is None or STATE_FOR.get(route) == "pool":
        return lead
    owner = cast.actors[role] if (role == "salesperson" and where == "own") else cast.owner[team]
    board.own(lead, owner)
    if route in STATE_FOR:
        board.set_lead(lead, state=STATE_FOR[route])
    return lead


def _call(cast: Cast, role: str, route: str, lead: uuid.UUID) -> Any:
    client = cast.clients[role]
    path = f"/api/leads/{lead}"
    if route == "read":
        return client.get(path)
    if route in ("note", "contact"):
        action = "call" if route == "contact" else None
        return client.post(f"{path}/notes", json={"text": "Spoke about Friday.", "action": action})
    if route == "profile":
        return client.put(f"{path}/profile", json={"need": "garage"})
    if route == "outcome":
        return client.post(f"{path}/outcome", json={"outcome": "won"})
    if route == "delete":
        return client.delete(path)
    return client.post(f"{path}/{route}")


def _events(board: Board, lead: uuid.UUID) -> list[uuid.UUID]:
    return [e.id for e in board.pipe.events(lead)]


def test_the_note_and_outcome_access_matrix(board: Board) -> None:
    cast = Cast(board)
    missing = cast.clients["admin"].get(f"/api/leads/{uuid.uuid4()}")
    results: dict[tuple[str, str, str], int] = {}
    for role in ROLES:
        for route in ROUTES:
            for where in WHERE:
                lead = _setup(board, cast, role, route, where)
                before = _events(board, lead)
                answer = _call(cast, role, route, lead)
                code = answer.status_code
                if route == "read" and code == 200 and "notes" not in answer.json():
                    code = 0
                results[(role, route, where)] = code
                if answer.status_code == 404:
                    assert answer.json() == missing.json(), (role, route, where)
                if answer.status_code >= 400 or route in ("read", "profile"):
                    assert _events(board, lead) == before, (role, route, where)
    expected = {key: ALLOWED.get(key, 404) for key in results}
    assert results == expected


FOLLOW_THROUGH = ("notes", "contacts", "profile", "outcomes")


def test_a_monitor_sees_the_lead_but_no_notes_profile_or_outcomes(board: Board) -> None:
    cast = Cast(board)
    lead = board.lead("north")
    board.own(lead, cast.owner["north"])
    lead_client = cast.clients["team_lead"]
    lead_client.post(f"/api/leads/{lead}/notes", json={"text": "Budget 400k.", "action": "call"})
    lead_client.put(f"/api/leads/{lead}/profile", json={"need": "garage"})
    detail = cast.clients["monitor"].get(f"/api/leads/{lead}")
    assert detail.status_code == 200
    body = detail.json()
    assert not set(FOLLOW_THROUGH) & set(body)
    assert "Budget 400k." not in detail.text
    assert "garage" not in detail.text
    full = cast.clients["admin"].get(f"/api/leads/{lead}").json()
    assert set(FOLLOW_THROUGH) <= set(full)
    assert full["notes"][0]["text"] == "Budget 400k."


# --- what an event may carry -------------------------------------------------------------

UUID = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}")
ALLOWED_KEYS: dict[str, set[str]] = {
    "lead.created": {"message_id", "team", "possible_duplicates", "rescued_by"},
    "lead.activity": {"message_id", "rescued_by", "to"},
    "lead.rescued": {"message_id", "by"},
    "lead.placed": {"by", "team"},
    "lead.claimed": {"owner_id"},
    "lead.released": {"by", "owner_id"},
    "lead.returned": {"by", "owner_id", "reason"},
    "lead.reassigned": {"by", "from", "to"},
    "lead.moved_team": {"by", "from_team", "to_team"},
    "note.added": {"note_id", "by"},
    "contact.logged": {"contact_id", "note_id", "kind", "by", "logged_by", "source"},
    "lead.outcome": {"by", "outcome", "reason"},
    "lead.reopened": {"by", "reason", "to", "message_id"},
    "lead.deleted": {"by"},
}
CODES = {
    "call", "text", "email", "meeting", "typed", "suggestion", "owned", "pool", "won", "lost",
    "not_real", "nurture", "buyer_wrote", "resumed", "owner_left", "price", "location",
    "timing", "bought_elsewhere", "not_ready", "unreachable", "other",
    "north", "south", "east", "west", "central",
}  # fmt: skip


def cast_list(value: Any) -> list[Any]:
    return list(value)


def _plain(value: Any) -> bool:
    """An id, a code, a list of ids, or nothing: never free text."""
    if value is None:
        return True
    if isinstance(value, list):
        return all(_plain(v) for v in cast_list(value))
    return isinstance(value, str) and (bool(UUID.fullmatch(value)) or value in CODES)


def test_event_payloads_carry_only_ids_and_codes(board: Board) -> None:
    cast = Cast(board)
    client = cast.clients["salesperson"]
    lead = board.lead("north", name="Zed Quill", email="zed@buyers.example")
    client.post(f"/api/leads/{lead}/claim")
    client.post(f"/api/leads/{lead}/notes", json={"text": "Called Zed, 400k.", "action": "call"})
    client.post(f"/api/leads/{lead}/nurture")
    client.post(f"/api/leads/{lead}/resume")
    body = {"outcome": "lost", "reason": "other", "reason_text": "Zed moved", "competitor": "X"}
    client.post(f"/api/leads/{lead}/outcome", json=body)
    board.pipe.deliver_form(
        "site-form",
        {"name": "Zed Quill", "email": "zed@buyers.example", "listing": "Cedar Point Lofts",
         "message": "Back again"},
    )  # fmt: skip
    client.post(f"/api/leads/{lead}/release")
    unassigned = board.lead(None)
    cast.clients["admin"].post(
        f"/api/leads/{unassigned}/place", json={"team_id": str(board.teams["north"])}
    )
    cast.clients["admin"].delete(f"/api/leads/{unassigned}")
    with board.sessions() as session:
        events = list(session.scalars(select(LeadEvent)))
    seen = {e.type for e in events}
    assert {"note.added", "contact.logged", "lead.outcome", "lead.reopened",
            "lead.deleted", "lead.activity"} <= seen  # fmt: skip
    for event in events:
        assert set(event.payload) <= ALLOWED_KEYS[event.type], event.type
        for key, value in event.payload.items():
            assert _plain(value), (event.type, key, value)
