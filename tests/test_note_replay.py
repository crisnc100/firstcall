"""The committed note recordings: the replay check finds the note reports, and the five F31
notes and the unit-page note replay from the recorded answers with no key."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from firstcall.evaluation.note_eval import (
    UNIT_DATE,
    UNIT_NOTES,
    ZONE,
    NoteRun,
    _gateway,  # pyright: ignore[reportPrivateUsage]
    read_unit_notes,
)
from firstcall.evaluation.run import throwaway_database
from firstcall.notes.reader import NoteReader
from firstcall.seed import seed as seed_world
from scripts.check_replay_report import committed

MODEL = "claude-haiku-5-5"
REPLAY = NoteRun(mode="replay", model=MODEL, budget_usd=Decimal("0"))


@pytest.fixture(autouse=True)
def no_ambient_model_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("FIRSTCALL_MODEL_"):
            monkeypatch.delenv(name)


def test_the_replay_check_finds_note_reports_and_skips_the_record_pass(tmp_path: Path) -> None:
    for name in (
        "2026-11-14-notes-seed-11.json",
        "2026-11-14-notes-seed-11.md",
        "2026-11-14-notes-seed-11-record-pass.json",
        "2026-11-14-notes-seed-7.json",
    ):
        (tmp_path / name).write_text("{}")
    assert committed(tmp_path) == [("notes", "2026-11-14", 7), ("notes", "2026-11-14", 11)]


def test_the_five_f31_notes_replay_with_no_suggestion(database_url: str) -> None:
    found = read_unit_notes(database_url, REPLAY)
    assert found == {
        "unit-future": [],
        "unit-negation": [],
        "unit-plan": [],
        "unit-hypothetical": [],
        "unit-colleague": [],
    }


def test_the_unit_page_note_replays_its_recorded_call(database_url: str) -> None:
    rows = [json.loads(line) for line in UNIT_NOTES.read_text().splitlines()]
    text = next(row["text"] for row in rows if row["id"] == "unit-page")
    with throwaway_database(database_url) as sessions:
        org = seed_world(sessions)
        reader = NoteReader(_gateway(sessions, REPLAY), detached=True)
        outcome = reader.read(org, None, text, UNIT_DATE, ZONE)
    assert outcome.answer is not None
    assert [(a.kind, a.quote) for a in outcome.answer.actions] == [
        ("call", "Called Dana this morning")
    ]
    assert outcome.answer.budget is not None
    assert (outcome.answer.budget.min, outcome.answer.budget.max) == (450000, 450000)
    assert outcome.answer.timeline_month == "2026-06"
