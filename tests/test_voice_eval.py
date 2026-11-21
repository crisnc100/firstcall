"""The voice eval runner (F38) on fake transcripts and fake answers: no Whisper, no key and no
model provider. Clips are a few of the voice gold's, copied with their own manifest."""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from firstcall.cli import app
from firstcall.corpus.notes import LABELS, SCRIPTS, read_jsonl
from firstcall.corpus.voice import CLIPS, spoken_rows, write_voice_manifest
from firstcall.evaluation.note_eval import IncompleteRecordingError, NoteRun
from firstcall.evaluation.note_score import baselines
from firstcall.evaluation.voice_eval import (
    ATLAS_TIMING,
    SIZES,
    ClipFailedError,
    Clips,
    TranscribedError,
    evaluate_pair,
    evaluate_voice,
    local_transcriber,
    machine,
    pick,
    read_transcripts,
    record_pair,
    run_path,
    spent,
    transcribe_pair,
    transcribed,
    transcripts_path,
    whisper_size,
    write_voice,
)
from firstcall.evaluation.voice_score import wer
from firstcall.model.gateway import ReplayMissError
from firstcall.model.synthetic import SyntheticIndex
from firstcall.model.transport import TransportRequest
from firstcall.voice.transcribe import (
    WHISPER_REVISIONS,
    LocalWhisper,
    UnusableAudioError,
    audio_key,
)
from scripts.check_replay_report import committed
from tests.model_fakes import MODEL, PRICES, FakeTransport
from tests.test_voice_transcribe import FakeModel
from tests.test_voice_transcribe import fake_whisper as fake_whisper  # a fixture, by name

SEED = 11
IDS = [f"note-11-{n:03d}" for n in range(1, 7)]
SPOKEN = {row["id"]: row["text"] for row in spoken_rows(SEED)}
MISHEARD = "note-11-002"
SILENT = "note-11-003"
SMALLEST = "the smallest size that meets every floor within the speed bound"


@pytest.fixture(autouse=True)
def no_ambient_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(("FIRSTCALL_MODEL_", "FIRSTCALL_TRANSCRIBE_", "FIRSTCALL_WHISPER_")):
            monkeypatch.delenv(name)


@pytest.fixture
def clips(tmp_path: Path) -> Clips:
    """Both clips of six seed-11 scripts, with a clip list and a manifest of their own."""
    gold = Clips()
    root = tmp_path / "clips"
    rows = [row for row in map(json.loads, (gold.folder / CLIPS).read_text().splitlines())
            if row["id"] in IDS]  # fmt: skip
    for row in rows:
        target = root / f"seed-{SEED}" / row["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(gold.folder / row["file"], target)
    (root / f"seed-{SEED}" / CLIPS).write_text("".join(json.dumps(r) + "\n" for r in rows))
    write_voice_manifest(root)
    return Clips(root, SEED)


class Heard:
    """A transcriber that hears each clip as its voice's spoken text, less what a test
    changes."""

    def __init__(self, clips: Clips, changed: dict[str, str] | None = None) -> None:
        self.texts: dict[str, str] = {}
        for condition in ("clean", "degraded"):
            for row in clips.rows(condition):
                audio = (clips.folder / row["file"]).read_bytes()
                text = (changed or {}).get(row["id"], SPOKEN[row["id"]])
                self.texts[audio_key(audio)] = text
        self.heard: list[str] = []

    def transcribe(self, audio: bytes, fmt: str) -> str:
        assert fmt == "mp3"
        self.heard.append(audio_key(audio))
        return self.texts[audio_key(audio)]


def _ticks(step: float = 0.75) -> Callable[[], float]:
    counter = itertools.count(0.0, step)
    return lambda: next(counter)


def _misheard() -> dict[str, str]:
    return {MISHEARD: SPOKEN[MISHEARD].replace("four hundred", "for hundred"), SILENT: "  \n "}


def _gold_reader() -> FakeTransport:
    """Answers each transcript with its script's gold, found by the text it was heard as."""
    scripts = read_jsonl(SEED, SCRIPTS)
    gold = baselines(scripts, read_jsonl(SEED, LABELS))["gold"]
    heard = [*SPOKEN.items(), *_misheard().items()]
    by_text = {" ".join(text.split()): i for i, text in heard if i in IDS and text.strip()}

    def respond(request: TransportRequest) -> str:
        found = re.search(r"<note>\n(.*)\n</note>", request.user, re.DOTALL)
        assert found is not None
        return json.dumps(gold[by_text[found.group(1)]])

    return FakeTransport(respond=respond)


def _run(
    mode: str, folder: Path, transport: FakeTransport | None = None, budget: str = "1.00"
) -> NoteRun:
    return NoteRun(mode=mode, model=MODEL, budget_usd=Decimal(budget), recordings=folder,
                   transport=transport, prices=PRICES)  # fmt: skip


def test_the_sizes_are_the_pinned_candidates_smallest_first() -> None:
    assert SIZES == ("tiny.en", "base.en", "small.en") == tuple(WHISPER_REVISIONS)
    assert whisper_size("base.en") == "base.en"
    with pytest.raises(ValueError, match="one of"):
        whisper_size("large-v3")


def test_a_pair_is_transcribed_once_with_the_seconds_each_clip_took(
    clips: Clips, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    heard = Heard(clips, _misheard())
    rows = transcribe_pair(clips, "base.en", "degraded", out, heard, timer=_ticks())
    degraded = clips.rows("degraded")
    assert [row["id"] for row in rows] == IDS == [row["id"] for row in degraded]
    assert len(heard.heard) == len(IDS)
    for row, clip in zip(rows, degraded, strict=True):
        assert row["sha256"] == audio_key((clips.folder / clip["file"]).read_bytes())
        assert row["compute_seconds"] == 0.75
    silent = next(row for row in rows if row["id"] == SILENT)
    assert (silent["text"], silent["error"]) == ("", "no speech")
    first = next(row for row in rows if row["id"] == IDS[0])
    assert (first["text"], first["error"]) == (SPOKEN[IDS[0]], None)
    path = transcripts_path(out, "base.en", "degraded")
    assert [json.loads(line) for line in path.read_text().splitlines()] == rows
    meta = json.loads(run_path(out, "base.en", "degraded").read_text())
    assert meta == {
        "seed": SEED,
        "size": "base.en",
        "condition": "degraded",
        "whisper_revision": WHISPER_REVISIONS["base.en"],
        "machine": machine(),
        "record_passes": [],
    }

    with pytest.raises(TranscribedError, match="once"):
        transcribe_pair(clips, "base.en", "degraded", out, heard)
    path.unlink()
    with pytest.raises(TranscribedError, match="once"):
        transcribe_pair(clips, "base.en", "degraded", out, heard)
    assert len(heard.heard) == len(IDS)
    assert not path.exists()


def test_whisper_is_built_through_the_transcribers_own_startup_check(
    fake_whisper: type[FakeModel], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIRSTCALL_TRANSCRIBE_MODE", "replay")
    whisper = local_transcriber("base.en", "/models")
    assert isinstance(whisper, LocalWhisper)
    assert (whisper.size, whisper.models) == ("base.en", "/models")
    # from_settings loaded the model and transcribed its generated clip before returning.
    assert [made["name"] for made in fake_whisper.made] == ["Systran/faster-whisper-base.en"]
    assert len(fake_whisper.calls) == 1


def test_the_models_folder_is_the_setting_unless_the_flag_names_one(
    fake_whisper: type[FakeModel], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIRSTCALL_WHISPER_MODELS", "/from-settings")
    from_settings = local_transcriber("base.en")
    assert isinstance(from_settings, LocalWhisper)
    assert from_settings.models == "/from-settings"
    flagged = local_transcriber("base.en", "/from-flag")
    assert isinstance(flagged, LocalWhisper)
    assert flagged.models == "/from-flag"


class Undecodable(Heard):
    """Hears every clip but one, which fails before Whisper hears it, as a decoder timeout
    does."""

    def transcribe(self, audio: bytes, fmt: str) -> str:
        if len(self.heard) == 2:
            raise UnusableAudioError("the audio does not decode")
        return super().transcribe(audio, fmt)


def test_a_clip_that_fails_before_whisper_hears_it_leaves_the_pair_untranscribed(
    clips: Clips, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    with pytest.raises(ClipFailedError, match=f"{IDS[2]}: the audio does not decode"):
        transcribe_pair(clips, "tiny.en", "clean", out, Undecodable(clips), _ticks())
    assert not out.exists()

    from firstcall.evaluation import voice_eval

    monkeypatch.setattr(voice_eval, "local_transcriber", lambda size, models: Undecodable(clips))
    result = CliRunner().invoke(app, [
        "eval-voice", "--size", "tiny.en", "--condition", "clean", "--transcribe-only",
        "--clips", str(clips.root), "--out", str(out),
    ])  # fmt: skip
    assert result.exit_code == 1
    assert f"{IDS[2]}: the audio does not decode" in result.output
    assert transcribed(out, "tiny.en", "clean") is False


def test_a_stop_between_the_transcripts_and_the_run_file_is_finished_on_the_next_run(
    clips: Clips, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    real = os.replace
    moves: list[Path] = []

    def stop_before_the_run_file(source: Path, target: Path) -> None:
        moves.append(Path(target))
        if len(moves) == 2:
            raise KeyboardInterrupt
        real(source, target)

    monkeypatch.setattr(os, "replace", stop_before_the_run_file)
    with pytest.raises(KeyboardInterrupt):
        transcribe_pair(clips, "tiny.en", "clean", out, Heard(clips), _ticks())
    monkeypatch.setattr(os, "replace", real)
    transcripts, run = transcripts_path(out, "tiny.en", "clean"), run_path(out, "tiny.en", "clean")
    assert moves == [transcripts, run]
    assert transcripts.exists()
    assert not run.exists()

    from firstcall.evaluation import voice_eval

    def never(size: str, models: str | None) -> None:
        raise AssertionError("the pair was transcribed again")

    monkeypatch.setattr(voice_eval, "local_transcriber", never)
    result = CliRunner().invoke(app, [
        "eval-voice", "--size", "tiny.en", "--condition", "clean", "--transcribe-only",
        "--clips", str(clips.root), "--out", str(out),
    ])  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "each pair is transcribed once" in result.output
    meta = json.loads(run.read_text())
    assert (meta["size"], meta["condition"], meta["record_passes"]) == ("tiny.en", "clean", [])
    assert transcribed(out, "tiny.en", "clean") is True

    run.unlink()
    with pytest.raises(TranscribedError, match="only one of"):
        transcribed(out, "tiny.en", "clean")
    result = CliRunner().invoke(app, [
        "eval-voice", "--size", "tiny.en", "--condition", "clean", "--transcribe-only",
        "--clips", str(clips.root), "--out", str(out),
    ])  # fmt: skip
    assert result.exit_code == 1
    assert "only one of" in result.output


def test_a_broken_setup_stops_the_run_before_any_clip(
    clips: Clips, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    out = tmp_path / "out"
    result = CliRunner().invoke(app, [
        "eval-voice", "--size", "tiny.en", "--condition", "degraded", "--transcribe-only",
        "--clips", str(clips.root), "--out", str(out),
    ])  # fmt: skip
    assert result.exit_code == 1
    assert "faster-whisper is not installed" in result.output
    assert not out.exists()


def test_every_metric_from_fake_transcripts_and_answers(
    clips: Clips, tmp_path: Path, database_url: str
) -> None:
    out, recordings = tmp_path / "out", tmp_path / "recordings"
    transcribe_pair(clips, "small.en", "degraded", out, Heard(clips, _misheard()), _ticks())
    transport = _gold_reader()
    result = record_pair(clips, "small.en", "degraded", database_url,
                         _run("record", recordings, transport), out)  # fmt: skip
    assert len(transport.requests) == len(IDS) - 1

    assert result["complete"] is True
    assert result["not_transcribed"] == [SILENT]
    assert (result["size"], result["condition"], result["seed"]) == ("small.en", "degraded", SEED)
    whole = result["score"]["all"]
    assert whole["scripts"] == len(IDS)
    assert whole["unreadable"] == 1
    assert {row["measure"] for row in whole["floors"]} >= {
        "budget F1", "key-term recall", "notes with an unsupported number or name, names by sound"
    }  # fmt: skip
    assert whole["strict"]["floor"] == "reported"
    assert set(result["score"]) == {"all", "seen", "unseen"}
    assert whole["actions"]["recall"]["interval"] is not None
    scripts = {s["id"]: s["text"] for s in read_jsonl(SEED, SCRIPTS)}
    heard = {**SPOKEN, MISHEARD: _misheard()[MISHEARD], SILENT: ""}
    assert result["wer"] == wer((scripts[i], heard[i]) for i in IDS)
    assert result["wer"]["errors"] > len(scripts[SILENT].split())
    audio = sum(row["seconds"] for row in clips.rows("degraded"))
    assert result["compute"] == {
        "clips": len(IDS),
        "audio_seconds": round(audio, 3),
        "compute_seconds": 0.75 * len(IDS),
        "per_minute": round(0.75 * len(IDS) / audio * 60, 2),
    }
    assert result["calls"]["calls"] == len(IDS) - 1
    meta = json.loads(run_path(out, "small.en", "degraded").read_text())
    [paid] = meta["record_passes"]
    assert (paid["calls"], paid["failed"], paid["model"], paid["unanswered"]) == (
        len(IDS) - 1, 0, MODEL, []
    )  # fmt: skip

    replayed = evaluate_pair(clips, "small.en", "degraded", database_url,
                             _run("replay", recordings), out)  # fmt: skip
    assert replayed["score"] == result["score"]
    assert len(transport.requests) == len(IDS) - 1


def test_record_mode_refuses_a_transcript_whose_audio_is_not_in_the_manifest(
    clips: Clips, tmp_path: Path, database_url: str
) -> None:
    out = tmp_path / "out"
    transcribe_pair(clips, "tiny.en", "clean", out, Heard(clips), _ticks())
    path = transcripts_path(out, "tiny.en", "clean")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    listed: dict[str, str] = json.loads(clips.manifest.read_text())["files"]
    for unlisted in ("0" * 64, listed[f"seed-{SEED}/{CLIPS}"]):
        rows[0] = {**rows[0], "sha256": unlisted, "text": "Called Pat about the big yard."}
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        transport = FakeTransport(default="{}")
        with pytest.raises(ValueError, match="is not of its clip's audio"):
            evaluate_pair(clips, "tiny.en", "clean", database_url,
                          _run("record", tmp_path / "recordings", transport), out)  # fmt: skip
        assert transport.requests == []


def _refused_before_any_read(
    clips: Clips, condition: str, out: Path, recordings: Path, url: str
) -> None:
    for mode in ("replay", "record"):
        transport = FakeTransport(default="{}")
        with pytest.raises(ValueError, match="is not of its clip's audio"):
            evaluate_pair(clips, "base.en", condition, url, _run(mode, recordings, transport),
                          out)  # fmt: skip
        assert transport.requests == []


def test_a_transcript_must_be_of_its_own_clips_audio_before_any_read(
    clips: Clips, tmp_path: Path, database_url: str
) -> None:
    out, recordings = tmp_path / "out", tmp_path / "recordings"
    transcribe_pair(clips, "base.en", "clean", out, Heard(clips), _ticks())
    clean = transcripts_path(out, "base.en", "clean")
    original = clean.read_text()
    rows = read_transcripts(clean)

    # The clean transcripts filed as the degraded ones: the same scripts, in the same order.
    transcripts_path(out, "base.en", "degraded").write_text(original)
    _refused_before_any_read(clips, "degraded", out, recordings, database_url)

    # One clip's transcript carrying the next clip's audio hash.
    swapped = [{**rows[0], "sha256": rows[1]["sha256"]}, *rows[1:]]
    clean.write_text("".join(json.dumps(row) + "\n" for row in swapped))
    _refused_before_any_read(clips, "clean", out, recordings, database_url)


def test_the_gold_index_takes_a_transcript_only_of_its_own_clip(
    clips: Clips, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    transcribe_pair(clips, "tiny.en", "clean", out, Heard(clips, _misheard()), _ticks())
    rows = read_transcripts(transcripts_path(out, "tiny.en", "clean"))
    misheard = next(row for row in rows if row["id"] == MISHEARD)
    text = misheard["text"].encode()
    index = SyntheticIndex.from_gold(transcripts=out / "transcripts", voice_manifest=clips.manifest)
    assert text in index
    # Its own text and hash, filed as the degraded clip's; then another clip's hash.
    other = {**misheard, "sha256": rows[0]["sha256"]}
    for name, row in (("tiny.en-degraded.jsonl", misheard), ("tiny.en-clean.jsonl", other)):
        folder = tmp_path / name / "transcripts"
        folder.mkdir(parents=True)
        (folder / name).write_text(json.dumps(row) + "\n")
        assert text not in SyntheticIndex.from_gold(transcripts=folder,
                                                    voice_manifest=clips.manifest)  # fmt: skip


def test_the_synthetic_index_takes_a_transcript_only_for_a_listed_clip(
    clips: Clips, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    transcribe_pair(clips, "tiny.en", "clean", out, Heard(clips), _ticks())
    # The spoken text says its figures in words, so it is no note script.
    listed = " ".join(SPOKEN[MISHEARD].split()).encode()
    assert listed not in SyntheticIndex.from_gold(transcripts=tmp_path / "none")
    index = SyntheticIndex.from_gold(transcripts=out / "transcripts", voice_manifest=clips.manifest)
    assert listed in index
    no_manifest = SyntheticIndex.from_gold(
        transcripts=out / "transcripts", voice_manifest=tmp_path / "none.json"
    )
    assert listed not in no_manifest
    no_transcripts = SyntheticIndex.from_gold(
        transcripts=tmp_path / "none", voice_manifest=clips.manifest
    )
    assert listed not in no_transcripts
    assert b"Called Pat about the big yard." not in index


def test_a_run_that_cannot_answer_every_transcript_writes_no_report(
    clips: Clips, tmp_path: Path, database_url: str
) -> None:
    out, recordings = tmp_path / "out", tmp_path / "recordings"
    transcribe_pair(clips, "tiny.en", "degraded", out, Heard(clips), _ticks())
    # A budget too small for every call leaves transcripts with no answer.
    run = _run("record", recordings, _gold_reader(), budget="0.004")
    with pytest.raises(IncompleteRecordingError, match="unanswered: note-11"):
        record_pair(clips, "tiny.en", "degraded", database_url, run, out)
    [paid] = json.loads(run_path(out, "tiny.en", "degraded").read_text())["record_passes"]
    assert paid["unanswered"]
    assert paid["calls"] < len(IDS)
    with pytest.raises(ReplayMissError):
        evaluate_voice(database_url, MODEL, clips, out, recordings)


def test_a_stopped_record_pass_keeps_its_spend_and_a_second_pays_only_for_the_rest(
    clips: Clips, tmp_path: Path, database_url: str
) -> None:
    out, recordings = tmp_path / "out", tmp_path / "recordings"
    transcribe_pair(clips, "tiny.en", "clean", out, Heard(clips), _ticks())
    gold = _gold_reader()
    assert gold.respond is not None
    answer = gold.respond

    def stop_on_the_third(request: TransportRequest) -> str:
        if len(stopping.requests) == 3:
            raise KeyboardInterrupt
        return answer(request)

    stopping = FakeTransport(respond=stop_on_the_third)
    with pytest.raises(KeyboardInterrupt):
        record_pair(clips, "tiny.en", "clean", database_url,
                    _run("record", recordings, stopping), out)  # fmt: skip
    path = run_path(out, "tiny.en", "clean")
    [stopped] = json.loads(path.read_text())["record_passes"]
    assert stopped["unanswered"] is None
    assert (stopped["ok"], stopped["failed"], stopped["reused"]) == (2, 0, 0)
    # The third call's reservation stays on the ledger, above the two answers' cost.
    assert Decimal(stopped["ledger_usd"]) > Decimal(stopped["cost_usd"]) > 0

    finishing = _gold_reader()
    record_pair(clips, "tiny.en", "clean", database_url, _run("record", recordings, finishing),
                out)  # fmt: skip
    assert len(finishing.requests) == len(IDS) - 2
    first, second = json.loads(path.read_text())["record_passes"]
    assert first == stopped
    assert (second["ok"], second["reused"], second["unanswered"]) == (len(IDS), 2, [])
    assert spent([first, second]) == {
        "passes": 2,
        "stopped": 1,
        "answers": len(IDS),
        "reused": 2,
        "failed": 0,
        "ledger_usd": f"{Decimal(first['ledger_usd']) + Decimal(second['ledger_usd']):.6f}",
    }


def test_answers_already_recorded_are_counted_as_reused_not_paid_for(
    clips: Clips, tmp_path: Path, database_url: str
) -> None:
    out, recordings = tmp_path / "out", tmp_path / "recordings"
    for size in ("tiny.en", "base.en"):
        transcribe_pair(clips, size, "clean", out, Heard(clips), _ticks())
    record_pair(clips, "tiny.en", "clean", database_url,
                _run("record", recordings, _gold_reader()), out)  # fmt: skip
    # base.en heard every clip as tiny.en did, so each read is already on file.
    unused = FakeTransport()
    record_pair(clips, "base.en", "clean", database_url, _run("record", recordings, unused),
                out)  # fmt: skip
    assert unused.requests == []
    [paid] = json.loads(run_path(out, "base.en", "clean").read_text())["record_passes"]
    assert (paid["ok"], paid["reused"], paid["ledger_usd"]) == (len(IDS), len(IDS), "0.000000")
    assert spent([paid])["answers"] == 0


def _atlas(per_minute: dict[str, float]) -> dict[str, Any]:
    return {
        "host": "a test host", "cpus": 2, "clips": 20, "condition": "degraded", "seed": 7,
        "date": "2026-01-01",
        "sizes": {size: {"clips": 20, "audio_seconds": 200.0, "compute_seconds": value / 60 * 200,
                         "per_minute": value} for size, value in per_minute.items()},
    }  # fmt: skip


def _record_all(clips: Clips, out: Path, recordings: Path, url: str) -> None:
    for size in SIZES:
        for condition in ("clean", "degraded"):
            changed = _misheard() if (size, condition) == ("tiny.en", "degraded") else {}
            changed = {k: v for k, v in changed.items() if k != SILENT}
            transcribe_pair(clips, size, condition, out, Heard(clips, changed), _ticks())
            record_pair(clips, size, condition, url, _run("record", recordings, _gold_reader()),
                        out)  # fmt: skip


def test_replay_rebuilds_the_report_byte_for_byte_and_it_picks(
    clips: Clips, tmp_path: Path, database_url: str
) -> None:
    out, recordings = tmp_path / "out", tmp_path / "recordings"
    _record_all(clips, out, recordings, database_url)
    with pytest.raises(ValueError, match=f"no {ATLAS_TIMING}"):
        write_voice(evaluate_voice(database_url, MODEL, clips, out, recordings), "2026-01-01",
                    tmp_path / "none")  # fmt: skip
    (out / ATLAS_TIMING).write_text(
        json.dumps(_atlas({"tiny.en": 4.0, "base.en": 7.5, "small.en": 30.0}))
    )

    report = evaluate_voice(database_url, MODEL, clips, out, recordings)
    assert report["complete"] is True
    assert set(report["pairs"]) == {f"{s}-{c}" for s in SIZES for c in ("clean", "degraded")}
    assert report["voices"] == Clips(clips.root, SEED).voices()
    assert sum(report["voices"].values()) == len(IDS)
    assert report["pick"] == {"size": "tiny.en", "rule": SMALLEST, "misses": []}
    first = write_voice(report, "2026-01-01", tmp_path / "first")
    again = write_voice(evaluate_voice(database_url, MODEL, clips, out, recordings), "2026-01-01",
                        tmp_path / "again")  # fmt: skip
    assert [p.name for p in first] == ["2026-01-01-voice-seed-11.json",
                                       "2026-01-01-voice-seed-11.md"]  # fmt: skip
    assert [p.read_bytes() for p in first] == [p.read_bytes() for p in again]
    text = first[1].read_text()
    assert "**Shipped size: `tiny.en`.**" in text
    assert "| tiny.en | degraded |" in text
    assert "names by spelling | reported |" in text
    assert "| budget F1 | >= 0.85 |" in text
    assert "Record passes: 1," in text
    assert "a test host" in text


def test_a_report_with_a_pair_missing_is_not_written(
    clips: Clips, tmp_path: Path, database_url: str
) -> None:
    out, recordings = tmp_path / "out", tmp_path / "recordings"
    transcribe_pair(clips, "tiny.en", "degraded", out, Heard(clips), _ticks())
    record_pair(clips, "tiny.en", "degraded", database_url,
                _run("record", recordings, _gold_reader()), out)  # fmt: skip
    (out / ATLAS_TIMING).write_text(json.dumps(_atlas(dict.fromkeys(SIZES, 1.0))))
    report = evaluate_voice(database_url, MODEL, clips, out, recordings)
    assert report["complete"] is False
    assert report["pick"] is None
    assert "small.en-degraded" in report["missing"]
    with pytest.raises(ValueError, match=r"incomplete \(missing tiny\.en-clean"):
        write_voice(report, "2026-01-01", tmp_path / "reports")
    assert not (tmp_path / "reports").exists()


def _fake(passes: dict[str, bool], per_minute: dict[str, float]) -> dict[str, Any]:
    def floors(met: bool) -> list[dict[str, Any]]:
        return [
            {"measure": "budget F1", "floor": ">= 0.85", "value": 0.9, "met": True},
            {"measure": "key-term recall", "floor": ">= 0.8", "value": 0.9 if met else 0.7,
             "met": met},
        ]  # fmt: skip

    return {
        "speed_bound": 60,
        "atlas": _atlas(per_minute),
        "pairs": {f"{s}-degraded": {"score": {"all": {"floors": floors(passes[s])}}}
                  for s in SIZES},
    }  # fmt: skip


def test_the_pick_rule_ships_the_smallest_size_that_passes_within_the_speed_bound() -> None:
    fast = {"tiny.en": 3.0, "base.en": 6.0, "small.en": 20.0}
    smallest = _fake({"tiny.en": True, "base.en": True, "small.en": True}, fast)
    assert pick(smallest)["size"] == "tiny.en"
    middle = _fake({"tiny.en": False, "base.en": True, "small.en": True}, fast)
    assert pick(middle) == {"size": "base.en", "rule": SMALLEST, "misses": []}
    # Within the bound means at most 60 seconds a minute.
    edge = _fake({"tiny.en": False, "base.en": False, "small.en": True},
                 {**fast, "small.en": 60.0})  # fmt: skip
    assert pick(edge)["size"] == "small.en"


def test_when_none_passes_the_largest_size_within_the_speed_bound_ships_with_its_misses() -> None:
    report = _fake(dict.fromkeys(SIZES, False), {"tiny.en": 3.0, "base.en": 6.0,
                                                  "small.en": 20.0})  # fmt: skip
    chosen = pick(report)
    assert chosen["size"] == "small.en"
    assert (
        chosen["rule"] == "no size meets every floor, so the largest within the speed bound ships"
    )
    assert [m["measure"] for m in chosen["misses"]] == ["key-term recall"]


def test_a_size_that_passes_but_is_too_slow_never_ships() -> None:
    slow_small = {"tiny.en": 3.0, "base.en": 6.0, "small.en": 61.0}
    report = _fake({"tiny.en": False, "base.en": False, "small.en": True}, slow_small)
    chosen = pick(report)
    assert chosen["size"] == "base.en"
    assert [m["measure"] for m in chosen["misses"]] == ["key-term recall"]
    slow_tiny = _fake({"tiny.en": True, "base.en": True, "small.en": True},
                      {"tiny.en": 61.0, "base.en": 6.0, "small.en": 20.0})  # fmt: skip
    assert pick(slow_tiny)["size"] == "base.en"
    none_fast = _fake(dict.fromkeys(SIZES, True), dict.fromkeys(SIZES, 90.0))
    assert pick(none_fast) == {"size": None, "rule": "no size runs within the speed bound",
                               "misses": []}  # fmt: skip


def test_the_replay_check_finds_voice_reports(tmp_path: Path) -> None:
    for name in ("2026-11-22-voice-seed-11.json", "2026-11-22-voice-seed-11.md",
                 "2026-11-14-notes-seed-11.json"):  # fmt: skip
        (tmp_path / name).write_text("{}")
    assert committed(tmp_path) == [("notes", "2026-11-14", 11), ("voice", "2026-11-22", 11)]


def test_the_machine_is_named_from_the_cpu(tmp_path: Path) -> None:
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor\t: 0\nmodel name\t: AMD EPYC 7763 64-Core Processor\n")
    assert machine(cpuinfo).startswith("AMD EPYC 7763 64-Core Processor, ")
    assert machine(tmp_path / "none").endswith(os.uname().machine)


@pytest.mark.parametrize(
    ("arguments", "says"),
    [
        (["--size", "large-v3", "--condition", "clean"], "one of"),
        (["--size", "tiny.en", "--condition", "noisy"], "condition"),
        (["--size", "tiny.en", "--condition", "clean", "--mode", "live"], "mode"),
        (["--size", "tiny.en", "--condition", "clean", "--budget", "10.01"], "$10"),
        (["--size", "tiny.en", "--condition", "clean", "--seed", "7"], "--out"),
        (["--size", "tiny.en", "--condition", "clean", "--clips", "elsewhere"], "--out"),
        (["--size", "tiny.en", "--condition", "clean", "--mode", "record"],
         "FIRSTCALL_MODEL_KEY"),
    ],
)  # fmt: skip
def test_the_command_refuses_before_any_transcription(arguments: list[str], says: str) -> None:
    result = CliRunner().invoke(app, ["eval-voice", *arguments])
    assert result.exit_code != 0
    assert says in result.output


def test_the_kill_switch_stops_a_record_run_before_any_transcription(
    clips: Clips, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from firstcall.evaluation import voice_eval

    def never(*args: object, **kwargs: object) -> None:
        raise AssertionError("the run went on while the model was off")

    for name in ("local_transcriber", "transcribe_pair", "evaluate_pair", "record_pair"):
        monkeypatch.setattr(voice_eval, name, never)
    monkeypatch.setenv("FIRSTCALL_MODEL_KEY", "not-a-real-key")
    monkeypatch.setenv("FIRSTCALL_MODEL_OFF", "1")
    result = CliRunner().invoke(app, [
        "eval-voice", "--size", "tiny.en", "--condition", "clean", "--mode", "record",
        "--clips", str(clips.root), "--out", str(tmp_path / "out"),
    ])  # fmt: skip
    assert result.exit_code == 2, result.output
    assert "FIRSTCALL_MODEL_OFF" in result.output
    assert not (tmp_path / "out").exists()


def test_the_report_command_writes_nothing_while_the_eval_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from firstcall.evaluation import voice_eval

    report = {"complete": False, "missing": ["tiny.en-clean"], "pairs": {}, "atlas": None,
              "pick": None}  # fmt: skip
    monkeypatch.setattr(voice_eval, "evaluate_voice", lambda url, model: report)
    result = CliRunner().invoke(app, ["eval-voice-report", "--date", "2026-01-01"])
    assert result.exit_code == 1
    assert "no report is written" in result.output
