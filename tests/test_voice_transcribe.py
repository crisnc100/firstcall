"""The transcribers: local Whisper with ADR-005's fixed settings and a pinned model, decoding
again so no file runs past three minutes; and replay, where a miss raises."""

from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from typing import Any, ClassVar

import pytest

from firstcall.board.notes import NOTE_MAX
from firstcall.settings import Settings
from firstcall.voice.transcribe import (
    WHISPER_REVISIONS,
    LocalWhisper,
    ReplayMissError,
    ReplayTranscripts,
    UnusableAudioError,
    from_settings,
    note_text,
)
from tests.voice_helpers import FIXTURES, fixture


class Segment:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeModel:
    made: ClassVar[list[dict[str, Any]]] = []
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, name: str, **options: Any) -> None:
        FakeModel.made.append({"name": name, **options})

    def transcribe(self, waveform: Any, **options: Any) -> tuple[list[Segment], None]:
        FakeModel.calls.append({"waveform": waveform, **options})
        return [Segment(" Called Ana "), Segment("this morning. ")], None


@pytest.fixture
def fake_whisper(monkeypatch: pytest.MonkeyPatch) -> type[FakeModel]:
    FakeModel.made, FakeModel.calls = [], []
    module = types.ModuleType("faster_whisper")
    monkeypatch.setattr(module, "WhisperModel", FakeModel, raising=False)
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return FakeModel


def test_local_whisper_runs_with_the_fixed_settings_and_a_pinned_model(
    fake_whisper: type[FakeModel],
) -> None:
    whisper = LocalWhisper("small.en", "/models")
    assert whisper.transcribe(fixture("ok.webm"), "webm") == "Called Ana this morning."
    whisper.transcribe(fixture("ok.wav"), "wav")
    assert fake_whisper.made == [
        {
            "name": "Systran/faster-whisper-small.en",
            "device": "cpu",
            "compute_type": "int8",
            "cpu_threads": 2,
            "download_root": "/models",
            "revision": WHISPER_REVISIONS["small.en"],
        }
    ]
    first = fake_whisper.calls[0]
    assert {k: v for k, v in first.items() if k != "waveform"} == {
        "language": "en",
        "beam_size": 5,
        "vad_filter": True,
        "condition_on_previous_text": False,
    }
    waveform = first["waveform"]
    assert str(waveform.dtype) == "float32"
    assert 31_000 < len(waveform) < 33_000
    assert 0.05 < float(abs(waveform).max()) <= 1.0


def test_local_whisper_refuses_audio_that_decodes_past_three_minutes(
    fake_whisper: type[FakeModel],
) -> None:
    whisper = LocalWhisper("tiny.en")
    with pytest.raises(UnusableAudioError, match="over 180 seconds"):
        whisper.transcribe(fixture("lying.webm"), "webm")
    with pytest.raises(UnusableAudioError, match="does not decode"):
        whisper.transcribe(b"OggS" + bytes(300), "ogg")
    assert fake_whisper.made == []


def test_only_the_three_candidates_are_sizes() -> None:
    assert sorted(WHISPER_REVISIONS) == ["base.en", "small.en", "tiny.en"]
    with pytest.raises(ValueError, match="size"):
        LocalWhisper("large-v3")


def test_replay_finds_a_transcript_by_the_audios_hash_and_a_miss_raises(tmp_path: Path) -> None:
    path = tmp_path / "transcripts.jsonl"
    replay = ReplayTranscripts(path)
    with pytest.raises(ReplayMissError):
        replay.transcribe(fixture("ok.webm"), "webm")
    key = hashlib.sha256(fixture("ok.webm")).hexdigest()
    path.write_text(
        json.dumps({"sha256": "0" * 64, "text": "Other."})
        + "\n"
        + json.dumps({"sha256": key, "text": "Called Ana."})
        + "\n"
    )
    assert replay.transcribe(fixture("ok.webm"), "webm") == "Called Ana."
    with pytest.raises(ReplayMissError, match="no recorded transcript"):
        replay.transcribe(fixture("ok.mp3"), "mp3")


def test_the_mode_comes_from_the_settings(fake_whisper: type[FakeModel]) -> None:
    replay = from_settings(Settings(transcribe_mode="replay", voice_transcripts="x.jsonl"))
    assert isinstance(replay, ReplayTranscripts)
    assert replay.path == Path("x.jsonl")
    local = from_settings(Settings(whisper_size="base.en", whisper_models="/models"))
    assert isinstance(local, LocalWhisper)
    assert (local.size, local.models) == ("base.en", "/models")
    assert Settings().transcribe_mode == "local"
    assert Settings().whisper_size == "small.en"


def test_a_transcript_becomes_one_line_of_note_text() -> None:
    assert note_text("  Called Ana\n this   morning. ") == "Called Ana this morning."
    with pytest.raises(UnusableAudioError, match="no speech"):
        note_text(" \n ")
    assert len(note_text("a" * NOTE_MAX)) == NOTE_MAX
    with pytest.raises(UnusableAudioError, match="over 5000"):
        note_text("a" * (NOTE_MAX + 1))


def test_the_page_tests_replayed_transcripts_match_their_audio() -> None:
    """The page test replays a transcript by its file's hash; a fixture remade without its
    row would leave that test waiting for a transcript that never comes (make.sh refreshes
    the rows)."""
    rows = [json.loads(line) for line in fixture("page-transcripts.jsonl").decode().splitlines()]
    assert sorted(row["file"] for row in rows) == ["ok.webm", "unit-page.webm"]
    for row in rows:
        assert row["sha256"] == hashlib.sha256(fixture(row["file"])).hexdigest(), row["file"]
        assert (
            ReplayTranscripts(FIXTURES / "page-transcripts.jsonl").transcribe(
                fixture(row["file"]), "webm"
            )
            == row["text"]
        )
