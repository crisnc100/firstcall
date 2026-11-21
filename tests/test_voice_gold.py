"""The voice gold: seed 11's note scripts as 300 clips, their voices and licences, the spoken
text, the manifest and size, and gold committed before any transcription code."""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter, defaultdict
from functools import cache
from typing import Any

import pytest

from firstcall.corpus.notes import LABELS, SCRIPTS, read_jsonl
from firstcall.corpus.voice import (
    CLIPS,
    CONDITIONS,
    SPOKEN,
    VOICE_DIR,
    VOICE_MANIFEST,
    VOICE_SEED,
    VOICES,
    plan_clips,
    read_voice_manifest,
    spoken_number,
    spoken_rows,
    spoken_text,
    voice_file_hashes,
)
from firstcall.evaluation.note_score import read_numbers
from firstcall.evaluation.voice_score import wer
from tests.test_corpus import ROOT, _first_commit_adding, _git
from tests.test_note_corpus import _gold_before_code

VOICE_GOLD = (
    "evals/gold/voice/MANIFEST.json",
    "evals/gold/voice/LICENSES.md",
    "evals/gold/voice/name-spellings.json",
    f"evals/gold/voice/seed-{VOICE_SEED}/{CLIPS}",
    f"evals/gold/voice/seed-{VOICE_SEED}/{SPOKEN}",
    "firstcall/evaluation/voice_score.py",
)
# Where PR4 puts transcription. Nothing here may exist before the voice gold and its scorer.
TRANSCRIPTION_CODE = ("firstcall/voice/transcribe.py",)
ADR_005 = "docs/adr/ADR-005-local-transcription.md"
MAX_BYTES = 15_000_000
REUSABLE = {"CC0", "CC BY 4.0", "CC BY-SA 4.0", "MIT", "public domain", "none"}
FOLDER = VOICE_DIR / f"seed-{VOICE_SEED}"


@cache
def clips() -> list[dict[str, Any]]:
    return [json.loads(line) for line in (FOLDER / CLIPS).read_text().splitlines()]


def test_there_are_two_clips_per_script_in_one_voice() -> None:
    ids = [s["id"] for s in read_jsonl(VOICE_SEED, SCRIPTS)]
    rows = clips()
    assert len(rows) == 300
    assert [(r["id"], r["condition"]) for r in rows] == [(i, c) for i in ids for c in CONDITIONS]
    voices: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        voices[row["id"]].add(row["voice"])
        assert row["file"] == f"{row['condition']}/{row['id']}.mp3"
    assert all(len(found) == 1 for found in voices.values())


def test_at_least_six_voices_read_evenly_and_some_speak_uk_english() -> None:
    per_voice = Counter(r["voice"] for r in clips() if r["condition"] == "clean")
    assert len(per_voice) >= 6
    assert set(per_voice.values()) == {15}
    assert any(voice.startswith("en_GB-") for voice in per_voice)


def test_the_clip_plan_reproduces_from_its_seed() -> None:
    committed = [{k: v for k, v in r.items() if k != "seconds"} for r in clips()]
    assert committed == plan_clips(VOICE_SEED)
    degraded = [r for r in clips() if r["condition"] == "degraded"]
    assert {r["speed"] for r in degraded} == {0.9, 1.1}
    assert all(r["noise_seed"] is not None for r in degraded)
    assert all(r["speed"] == 1.0 and r["noise_seed"] is None for r in clips()
               if r["condition"] == "clean")  # fmt: skip


def _licence_rows() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in (VOICE_DIR / "LICENSES.md").read_text().splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        match = re.fullmatch(r"`([^`]+)`", cells[0]) if line.startswith("|") else None
        if match:
            rows[match.group(1)] = cells
    return rows


def test_every_voice_is_listed_with_a_licence_that_allows_reuse() -> None:
    listed = _licence_rows()
    assert set(listed) == {voice.id for voice in VOICES}
    assert {r["voice"] for r in clips()} == set(listed)
    for voice, cells in listed.items():
        licence, base_licence = cells[3], cells[5]
        assert licence in REUSABLE - {"none"}, voice
        assert base_licence in REUSABLE, voice


def _mp3_header(data: bytes) -> tuple[int, str]:
    """The first MPEG audio frame's sample rate and channel mode, after any ID3v2 tag."""
    start = 0
    if data[:3] == b"ID3":
        size = 0
        for byte in data[6:10]:
            size = size << 7 | byte
        start = 10 + size
    header = int.from_bytes(data[start : start + 4], "big")
    assert header >> 21 == 0x7FF, "no frame sync"
    version, layer = header >> 19 & 0b11, header >> 17 & 0b11
    assert (version, layer) == (0b10, 0b01), "not MPEG-2 Layer III"
    rate = {0: 22050, 1: 24000, 2: 16000}[header >> 10 & 0b11]
    mode = "mono" if header >> 6 & 0b11 == 0b11 else "stereo"
    return rate, mode


def test_every_clip_is_a_16_khz_mono_mp3_under_three_minutes() -> None:
    for row in clips():
        path = FOLDER / row["file"]
        assert _mp3_header(path.read_bytes()) == (16000, "mono"), row["file"]
        assert 1 < row["seconds"] < 180, row["file"]
    listed = {r["file"] for r in clips()}
    on_disk = {p.relative_to(FOLDER).as_posix() for p in FOLDER.rglob("*.mp3")}
    assert on_disk == listed


def test_the_spoken_text_is_the_script_with_its_figures_in_words() -> None:
    committed = [json.loads(line) for line in (FOLDER / SPOKEN).read_text().splitlines()]
    assert committed == spoken_rows(VOICE_SEED)
    scripts = {s["id"]: s["text"] for s in read_jsonl(VOICE_SEED, SCRIPTS)}
    for row in committed:
        assert not re.search(r"\d", row["text"]), row["id"]
        assert read_numbers(row["text"]) == read_numbers(scripts[row["id"]]), row["id"]
    assert spoken_text("Budget is between $425k and 450 grand.") == (
        "Budget is between four hundred twenty-five thousand dollars and four hundred fifty "
        "thousand."
    )
    assert spoken_text("Hard ceiling of $1.25M.") == (
        "Hard ceiling of one million two hundred fifty thousand dollars."
    )


def test_every_gold_budget_figure_reads_back_from_the_spoken_text() -> None:
    spoken = {
        row["id"]: row["text"]
        for row in map(json.loads, (FOLDER / SPOKEN).read_text().splitlines())
    }
    checked = 0
    for label in read_jsonl(VOICE_SEED, LABELS):
        if label["budget"] is None:
            continue
        numbers = read_numbers(spoken[label["id"]])
        for bound in ("min", "max"):
            if label["budget"][bound] is not None:
                assert label["budget"][bound] in numbers, label["id"]
                checked += 1
    assert checked >= 87


def test_the_spoken_text_has_no_word_errors_against_its_script() -> None:
    """The word error rate's reading of numbers agrees with the spoken-text rule, so a
    transcript is never charged for saying a figure in words."""
    scripts = {s["id"]: s["text"] for s in read_jsonl(VOICE_SEED, SCRIPTS)}
    pairs = [(scripts[r["id"]], r["text"]) for r in spoken_rows(VOICE_SEED)]
    assert wer(pairs)["errors"] == 0


@pytest.mark.parametrize(
    "value",
    [
        1,
        19,
        20,
        99,
        105,
        425_000,
        785_000,
        1_000_000,
        1_250_000,
        999_995_000,
        2_000_000_000,
    ],
)
def test_spoken_numbers_read_back_to_their_value(value: int) -> None:
    assert read_numbers(spoken_number(value)) == {value}


def test_spoken_numbers_are_positive_and_whole() -> None:
    with pytest.raises(ValueError, match="positive"):
        spoken_number(0)
    with pytest.raises(ValueError, match="whole"):
        spoken_text("About $1.2345k.")


def test_the_voice_manifest_matches_every_gold_file() -> None:
    manifest = read_voice_manifest()
    assert manifest == voice_file_hashes()
    assert len(manifest) == 300 + 4
    assert VOICE_MANIFEST.name not in manifest


def test_the_voice_folder_is_under_15_mb() -> None:
    size = sum(path.stat().st_size for path in VOICE_DIR.rglob("*") if path.is_file())
    assert size < MAX_BYTES, size


def test_voice_gold_and_its_scorer_were_committed_before_any_transcription_code() -> None:
    """Checked from git history, as the note and message gold are."""
    assert _git("rev-parse", "--is-shallow-repository") == "false", "needs full history"
    _gold_before_code(VOICE_GOLD, TRANSCRIPTION_CODE)


def _adr_with_the_gold() -> str:
    """ADR-005 as it stood when the voice gold was first committed, or as it stands now when
    the gold is not committed yet."""
    commit = _first_commit_adding(VOICE_GOLD[0])
    if commit is None:
        return (ROOT / ADR_005).read_text()
    return subprocess.run(
        ["git", "show", f"{commit}:{ADR_005}"], cwd=ROOT, capture_output=True, text=True,
        check=True,
    ).stdout  # fmt: skip


def test_adr_005_fixed_candidates_settings_and_the_speed_bound_with_the_gold() -> None:
    text = _adr_with_the_gold()
    for fixed in ("`tiny.en`", "`base.en`", "`small.en`", "`int8`", "2 threads", "beam 5",
                  "language `en`", "voice activity filter on",
                  "60 compute seconds per audio minute"):  # fmt: skip
        assert fixed in text, fixed
    assert "- **Result:** pending" in text
