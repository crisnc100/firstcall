"""Who may record a voice memo (F36): exactly who may write a note, and log its contact
when they may log one. Everyone else gets the 404 of a lead that does not exist."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import VoiceAudio
from tests.board_helpers import Board
from tests.conftest import FakeClock
from tests.test_note_access import ROLES, TEAM, WHERE, Cast
from tests.voice_helpers import counts, upload

ROUTES = ("voice", "voice_contact")
ALLOWED: dict[tuple[str, str, str], int] = {
    **{(role, route, "own"): 201 for role in ("salesperson", "team_lead") for route in ROUTES},
    **{("admin", "voice", where): 201 for where in WHERE},
    **{("admin", "voice_contact", where): 201 for where in ("own", "other")},
    # An admin may note an Unassigned lead, but it has no owner, so no contact.
    ("admin", "voice_contact", "unassigned"): 422,
}


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    clock.current = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
    return Board(sessions, clock)


def _lead(board: Board, cast: Cast, role: str, where: str) -> uuid.UUID:
    team = TEAM[where]
    lead = board.lead(team)
    if team is not None:
        mine = role == "salesperson" and where == "own"
        board.own(lead, cast.actors[role] if mine else cast.owner[team])
    return lead


def test_the_voice_memo_access_matrix(board: Board) -> None:
    cast = Cast(board)
    missing = cast.clients["admin"].get(f"/api/leads/{uuid.uuid4()}")
    results: dict[tuple[str, str, str], int] = {}
    for role in ROLES:
        for route in ROUTES:
            for where in WHERE:
                lead = _lead(board, cast, role, where)
                before = counts(board)
                action = "call" if route == "voice_contact" else None
                answer: Any = upload(cast.clients[role], lead, action=action)
                results[(role, route, where)] = answer.status_code
                if answer.status_code == 404:
                    assert answer.json() == missing.json(), (role, route, where)
                if answer.status_code >= 400:
                    assert counts(board) == before, (role, route, where)
                # The memo is done waiting, so the per-person cap never answers here.
                with board.sessions.begin() as session:
                    session.execute(delete(VoiceAudio))
    assert results == {key: ALLOWED.get(key, 404) for key in results}


def test_a_signed_out_or_forged_upload_stores_nothing(board: Board) -> None:
    cast = Cast(board)
    lead = _lead(board, cast, "salesperson", "own")
    before = counts(board)
    assert upload(board.client(), lead).status_code == 401
    forged = cast.clients["salesperson"]
    token = forged.headers.pop("X-CSRF-Token")
    assert upload(forged, lead).status_code == 403
    forged.headers["X-CSRF-Token"] = token
    assert upload(cast.clients["salesperson"], board.lead(None)).status_code == 404
    assert counts(board)["notes"] == before["notes"]
    assert counts(board)["voice_audio"] == 0
