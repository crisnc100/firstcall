"""Note reads through the gateway and the note eval runner (F32), on fake answers: no test
talks to a model provider."""

from __future__ import annotations

import json
import os
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from firstcall.corpus.notes import LABELS, NOTES_DIR, SCRIPTS, read_jsonl
from firstcall.evaluation.note_eval import NoteRun, evaluate_notes, prompt_sha256, write_notes
from firstcall.evaluation.note_score import baselines
from firstcall.model.gateway import NotSyntheticError, ReplayMissError
from firstcall.model.prompts import READ_NOTE, SYSTEM_PROMPTS
from firstcall.model.synthetic import SyntheticIndex
from firstcall.model.transport import TransportRequest
from firstcall.notes.reader import NoteReader
from firstcall.seed import seed
from tests.conftest import FakeClock
from tests.model_fakes import MODEL, PRICES, FakeTransport, gateway


@pytest.fixture(autouse=True)
def no_ambient_model_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("FIRSTCALL_MODEL_"):
            monkeypatch.delenv(name)


def _gold_answers(seed_number: int) -> dict[str, str]:
    """Each script's text mapped to its gold written as an answer."""
    scripts = read_jsonl(seed_number, SCRIPTS)
    gold = baselines(scripts, read_jsonl(seed_number, LABELS))["gold"]
    return {s["text"]: json.dumps(gold[s["id"]]) for s in scripts}


def _answering(seed_number: int) -> FakeTransport:
    answers = _gold_answers(seed_number)

    def respond(request: TransportRequest) -> str:
        found = re.search(r"<note>\n(.*)\n</note>", request.user, re.DOTALL)
        assert found is not None
        return answers[found.group(1)]

    return FakeTransport(respond=respond)


def test_a_note_read_is_recorded_in_its_own_file_and_replayed(
    sessions: sessionmaker[Session], clock: FakeClock, tmp_path: Path
) -> None:
    org = seed(sessions)
    script = read_jsonl(7, SCRIPTS)[0]
    recorder = _answering(7)
    record = gateway(sessions, clock, recorder, mode="record", recordings=tmp_path,
                     synthetic=SyntheticIndex.from_gold())  # fmt: skip
    reader = NoteReader(record, detached=True)
    outcome = reader.read(org, None, script["text"], script["note_date"], "America/New_York")
    assert outcome.answer is not None
    [sent] = recorder.requests
    assert sent.system == SYSTEM_PROMPTS[READ_NOTE]
    assert sent.user.endswith(f"<note>\n{script['text']}\n</note>")
    assert [p.name for p in tmp_path.iterdir()] == ["notes-test__model.jsonl"]

    replay = gateway(sessions, clock, FakeTransport(), mode="replay", recordings=tmp_path)
    again = NoteReader(replay).read(org, None, script["text"], script["note_date"],
                                    "America/New_York")  # fmt: skip
    assert again == outcome


def test_record_mode_reads_only_synthetic_notes(
    sessions: sessionmaker[Session], clock: FakeClock, tmp_path: Path
) -> None:
    org = seed(sessions)
    transport = FakeTransport(default="{}")
    record = gateway(sessions, clock, transport, mode="record", recordings=tmp_path,
                     synthetic=SyntheticIndex.from_gold())  # fmt: skip
    reader = NoteReader(record)
    unit = json.loads((NOTES_DIR.parent / "unit-notes.jsonl").read_text().splitlines()[0])
    reader.read(org, None, unit["text"], "2026-03-02", "UTC")
    assert len(transport.requests) == 1
    with pytest.raises(NotSyntheticError):
        reader.read(org, None, "Called my real client Jo at 555-0100.", "2026-03-02", "UTC")
    assert len(transport.requests) == 1


def test_a_note_read_logs_its_note(sessions: sessionmaker[Session], clock: FakeClock) -> None:
    from sqlalchemy import select

    from firstcall.db import ModelCall

    org = seed(sessions)
    live = gateway(sessions, clock, FakeTransport(default="{}"))
    note_id = uuid.uuid4()
    request = NoteReader(live).request(org, note_id, "Called Ana.", "2026-03-02", "UTC")
    assert request.note_id == note_id
    assert NoteReader(live, detached=True).request(org, note_id, "x", "2026-03-02",
                                                   "UTC").note_id is None  # fmt: skip
    NoteReader(live, detached=True).read(org, None, "Called Ana.", "2026-03-02", "UTC")
    with sessions() as session:
        assert session.scalars(select(ModelCall.note_id)).all() == [None]


def _run(
    mode: str, folder: Path, transport: FakeTransport | None = None, budget: str = "1.00"
) -> NoteRun:
    return NoteRun(mode=mode, model=MODEL, budget_usd=Decimal(budget), recordings=folder,
                   transport=transport, prices=PRICES)  # fmt: skip


def test_the_runner_records_then_replays_every_metric_split_and_baseline(
    database_url: str, tmp_path: Path
) -> None:
    folder = tmp_path / "recordings"
    transport = _answering(7)
    recorded = evaluate_notes(7, database_url, _run("record", folder, transport), limit=30)
    calls = len(transport.requests)
    assert calls == 30
    replayed = evaluate_notes(7, database_url, _run("replay", folder), limit=30)
    assert len(transport.requests) == calls
    assert replayed["score"] == recorded["score"]

    report = replayed
    assert report["seed"] == 7
    assert report["prompt_sha256"] == prompt_sha256()
    assert report["complete"] is True
    whole = report["score"]["all"]
    assert whole["scripts"] == 30
    assert set(whole["fields"]) == {"budget", "authority", "need", "timeline"}
    assert whole["fields"]["budget"]["f1_interval"] is not None
    assert {row["measure"] for row in whole["floors"]} >= {"budget F1", "key-term recall"}
    assert whole["unsupported"]["count"] == 0
    assert set(report["score"]) == {"all", "seen", "unseen"}
    assert set(report["baselines"]) == {"always_fill", "never_fill", "copy_through", "gold"}
    assert report["baselines"]["never_fill"]["met"] is False
    assert report["calls"]["calls"] == 30

    written = write_notes(report, "2026-01-01", tmp_path / "reports")
    rewritten = write_notes(
        evaluate_notes(7, database_url, _run("replay", folder), limit=30),
        "2026-01-01",
        tmp_path / "again",
    )
    assert [p.read_bytes() for p in written] == [p.read_bytes() for p in rewritten]
    assert [p.name for p in written] == ["2026-01-01-notes-seed-7.json",
                                         "2026-01-01-notes-seed-7.md"]  # fmt: skip


def test_an_unreadable_answer_is_scored_empty_and_counted(
    database_url: str, tmp_path: Path
) -> None:
    report = evaluate_notes(
        7, database_url, _run("record", tmp_path, FakeTransport(default="no json")), limit=5
    )
    assert report["score"]["all"]["unreadable"] == 5
    assert report["complete"] is True


def test_a_replay_miss_stops_the_run(database_url: str, tmp_path: Path) -> None:
    with pytest.raises(ReplayMissError):
        evaluate_notes(7, database_url, _run("replay", tmp_path), limit=3)


def test_a_run_that_cannot_answer_every_script_writes_no_report(
    database_url: str, tmp_path: Path
) -> None:
    # A budget too small for every call leaves scripts with no answer.
    run = _run("record", tmp_path, _answering(7), budget="0.004")
    report = evaluate_notes(7, database_url, run, limit=10)
    assert report["complete"] is False
    with pytest.raises(ValueError, match="incomplete"):
        write_notes(report, "2026-01-01", tmp_path / "reports")
