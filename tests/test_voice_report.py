"""The committed voice eval (F38): ADR-005's pick rule applied to the committed report gives
the size the product ships, seed 11 ran once per size and condition with 150 reads under the
frozen note prompt, and the misses are published."""

from __future__ import annotations

import json
import re
from functools import cache
from typing import Any

import yaml

from firstcall.corpus.voice import CONDITIONS, read_voice_manifest
from firstcall.evaluation.note_eval import prompt_sha256
from firstcall.evaluation.report import REPORTS
from firstcall.evaluation.run import REPO_ROOT
from firstcall.evaluation.voice_eval import (
    SIZES,
    VOICE_RUNS,
    Clips,
    pick,
    read_transcripts,
    run_path,
    spent,
    transcripts_path,
)
from firstcall.settings import Settings
from firstcall.voice.transcribe import WHISPER_REVISIONS

FAILURES = REPO_ROOT / "docs" / "failures"
REPORT = REPORTS / "2026-11-21-voice-seed-11.json"


@cache
def committed() -> dict[str, Any]:
    return json.loads(REPORT.read_text())


def _compose_default() -> str:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    value = compose["services"]["transcriber"]["environment"]["FIRSTCALL_WHISPER_SIZE"]
    found = re.fullmatch(r"\$\{FIRSTCALL_WHISPER_SIZE:-(?P<size>[^}]+)\}", value)
    assert found is not None, value
    return found["size"]


def test_the_pick_rule_on_the_committed_report_gives_the_shipped_size() -> None:
    report = committed()
    chosen = pick(report)
    assert chosen == report["pick"]
    assert chosen["size"] is not None
    assert chosen["size"] == Settings.model_fields["whisper_size"].default
    assert chosen["size"] == _compose_default()


def test_seed_11_ran_once_per_pair_with_150_reads_under_the_frozen_prompt() -> None:
    report = committed()
    notes = json.loads((REPORTS / "2026-11-14-notes-seed-11.json").read_text())
    assert report["prompt_sha256"] == notes["prompt_sha256"] == prompt_sha256()
    assert set(report["pairs"]) == {f"{s}-{c}" for s in SIZES for c in CONDITIONS}
    audio = read_voice_manifest()
    clips = Clips()
    for pair in report["pairs"].values():
        size, condition = pair["size"], pair["condition"]
        assert pair["prompt_sha256"] == report["prompt_sha256"], (size, condition)
        assert pair["calls"]["calls"] == 150, (size, condition)
        assert pair["structured_output"] is True, (size, condition)
        rows = read_transcripts(transcripts_path(VOICE_RUNS, size, condition))
        own = [audio[f"{clips.folder.name}/{clip['file']}"] for clip in clips.rows(condition)]
        assert [row["id"] for row in rows] == [row["id"] for row in clips.rows(condition)]
        assert [row["sha256"] for row in rows] == own, (size, condition)
        meta = json.loads(run_path(VOICE_RUNS, size, condition).read_text())
        assert (meta["seed"], meta["whisper_revision"]) == (11, WHISPER_REVISIONS[size])
        # A stopped pass may be resumed by another; the last one ended with every
        # transcript answered, and the report adds them all up.
        assert meta["record_passes"], (size, condition)
        assert meta["record_passes"][-1]["unanswered"] == [], (size, condition)
        assert pair["record_pass"] == spent(meta["record_passes"]), (size, condition)


def test_every_miss_of_the_shipped_size_is_published() -> None:
    chosen = committed()["pick"]
    listed = (FAILURES / "README.md").read_text()
    assert "0002-voice-seed-11.md" in listed
    text = (FAILURES / "0002-voice-seed-11.md").read_text()
    assert f"`{chosen['size']}`" in text
    for miss in chosen["misses"]:
        assert str(miss["value"]) in text, miss
