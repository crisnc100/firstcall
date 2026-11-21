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
    transcripts_path,
)
from firstcall.settings import Settings
from firstcall.voice.transcribe import WHISPER_REVISIONS

FAILURES = REPO_ROOT / "docs" / "failures"


@cache
def committed() -> dict[str, Any]:
    [path] = sorted(REPORTS.glob("*-voice-seed-11.json"))
    return json.loads(path.read_text())


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
    audio = set(read_voice_manifest().values())
    clips = Clips()
    for pair in report["pairs"].values():
        size, condition = pair["size"], pair["condition"]
        assert pair["prompt_sha256"] == report["prompt_sha256"], (size, condition)
        assert pair["calls"]["calls"] == 150, (size, condition)
        assert pair["structured_output"] is True, (size, condition)
        assert pair["record_pass"]["passes"] == 1, (size, condition)
        rows = read_transcripts(transcripts_path(VOICE_RUNS, size, condition))
        assert [row["id"] for row in rows] == [row["id"] for row in clips.rows(condition)]
        assert all(row["sha256"] in audio for row in rows), (size, condition)
        meta = json.loads(run_path(VOICE_RUNS, size, condition).read_text())
        assert (meta["seed"], meta["whisper_revision"]) == (11, WHISPER_REVISIONS[size])


def test_every_miss_of_the_shipped_size_is_published() -> None:
    chosen = committed()["pick"]
    listed = (FAILURES / "README.md").read_text()
    assert "0002-voice-seed-11.md" in listed
    text = (FAILURES / "0002-voice-seed-11.md").read_text()
    assert f"`{chosen['size']}`" in text
    for miss in chosen["misses"]:
        assert str(miss["value"]) in text, miss
