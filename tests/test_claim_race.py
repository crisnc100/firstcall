"""Twenty claims at once give one owner and nineteen "already claimed" answers (F21)."""

from __future__ import annotations

import threading
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from tests.board_helpers import Board
from tests.conftest import FakeClock

RUNS = 20
CLAIMERS = 20


def _claim(client: Any, lead: Any, barrier: threading.Barrier, answers: list[Any], n: int) -> None:
    barrier.wait()
    answers[n] = client.post(f"/api/leads/{lead}/claim")


def test_twenty_concurrent_claims_give_one_owner(
    sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    board = Board(sessions, clock)
    users = [
        board.user(f"seller{n}@larkspur.example", teams={"north": "salesperson"})
        for n in range(CLAIMERS)
    ]
    clients = [board.signed_in(user.email) for user in users]
    for run in range(RUNS):
        lead = board.lead("north")
        start = threading.Barrier(CLAIMERS)
        answers: list[Any] = [None] * CLAIMERS

        threads = [
            threading.Thread(target=_claim, args=(clients[n], lead, start, answers, n))
            for n in range(CLAIMERS)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        codes = sorted(answer.status_code for answer in answers)
        assert codes == [200] + [409] * (CLAIMERS - 1), f"run {run}"
        assert {a.json()["detail"] for a in answers if a.status_code == 409} == {"already claimed"}
        winner = next(n for n, a in enumerate(answers) if a.status_code == 200)
        saved = board.get(lead)
        assert (saved.state, saved.owner_id) == ("owned", users[winner].id), f"run {run}"
        assert [e.type for e in board.pipe.events(lead)] == ["lead.created", "lead.claimed"]
