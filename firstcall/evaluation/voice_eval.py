"""The voice eval (F38): seed 11's memos through each candidate Whisper size, each transcript
read by the note reader with its frozen prompt and scored against the script its voice read
(ADR-005).

A run is one size on one condition, clean or degraded, in two steps:
1. Transcribe every clip on this machine, free. The transcriber is built by `from_settings`,
   the transcriber process's own startup check, so a missing ffmpeg or model stops the run
   before the first clip instead of being scored as bad audio. The transcripts and the
   seconds each took go to transcripts/<size>-<condition>.jsonl, which is never written over:
   a pair is transcribed once.
2. Read each transcript through the gateway as the note eval reads a script; record mode pays
   for each answer not yet recorded. Before any read, each transcript must be of its own
   clip's audio, as the voice manifest hashes it, and record mode sends only those.

The report is built in replay mode from the transcripts, the recorded answers, each pair's
record passes and the Atlas timing, so CI writes it again byte for byte with no Whisper and
no key. It applies ADR-005's pick rule.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, get_args

from firstcall.core.delivery import sha256
from firstcall.corpus.notes import LABELS, SCRIPTS, read_jsonl
from firstcall.corpus.voice import CLIPS, CONDITIONS, VOICE_DIR, VOICE_MANIFEST, VOICE_SEED
from firstcall.evaluation import voice_score
from firstcall.evaluation.metrics import wilson
from firstcall.evaluation.note_eval import (
    RECORDINGS,
    ZONE,
    IncompleteRecordingError,
    NoteRun,
    _calls,
    _gateway,
    _json_prompt_answers,
    prompt_sha256,
)
from firstcall.evaluation.note_score import BANT_FIELDS
from firstcall.evaluation.report import REPORTS
from firstcall.evaluation.run import REPO_ROOT, throwaway_database
from firstcall.model.gateway import ReplayMissError
from firstcall.model.prompts import READ_NOTE
from firstcall.model.synthetic import VOICE_TRANSCRIPTS, SyntheticIndex, clip_audio
from firstcall.notes.reader import UNREADABLE, NoteReader
from firstcall.settings import Settings, WhisperSize
from firstcall.voice.transcribe import (
    BEAM_SIZE,
    COMPUTE_TYPE,
    CPU_THREADS,
    LANGUAGE,
    WHISPER_REVISIONS,
    Transcriber,
    UnusableAudioError,
    audio_key,
    from_settings,
    note_text,
)

VOICE_RUNS = REPO_ROOT / "evals" / "voice"
TRANSCRIPTS = VOICE_TRANSCRIPTS.name
RUNS = "runs"
ATLAS_TIMING = "atlas-timing.json"
SIZES: tuple[WhisperSize, ...] = get_args(WhisperSize)
# ADR-005's speed bound: compute seconds per minute of audio on the beta host, 2 CPUs.
SPEED_BOUND = 60
SETTINGS = {
    "compute_type": COMPUTE_TYPE,
    "cpu_threads": CPU_THREADS,
    "language": LANGUAGE,
    "beam_size": BEAM_SIZE,
}


class TranscribedError(Exception):
    """This pair already has transcripts or a run file, and a pair is transcribed once."""


class ClipFailedError(Exception):
    """A clip failed before Whisper heard it: it did not decode, or decoding took too long.
    That says nothing about the size, so the pair is left untranscribed, to run again."""


@dataclass(frozen=True)
class Clips:
    """Rendered memos: <root>/seed-<N>/clips.jsonl, the audio it lists, and <root>/MANIFEST.json
    hashing them. The voice gold by default; the seed-7 dry run renders its own folder."""

    root: Path = VOICE_DIR
    seed: int = VOICE_SEED

    @property
    def folder(self) -> Path:
        return self.root / f"seed-{self.seed}"

    @property
    def manifest(self) -> Path:
        return self.root / VOICE_MANIFEST.name

    def rows(self, condition: str) -> list[dict[str, Any]]:
        lines = (self.folder / CLIPS).read_text().splitlines()
        return [row for row in map(json.loads, lines) if row["condition"] == condition]

    def voices(self) -> dict[str, int]:
        """Each voice and how many scripts it read."""
        lines = (self.folder / CLIPS).read_text().splitlines()
        read = {(row["id"], row["voice"]) for row in map(json.loads, lines)}
        return dict(sorted(Counter(voice for _, voice in read).items()))


def whisper_size(name: str) -> WhisperSize:
    for size in SIZES:
        if size == name:
            return size
    raise ValueError(f"the Whisper size must be one of {', '.join(SIZES)}")


def transcripts_path(out: Path, size: str, condition: str) -> Path:
    return out / TRANSCRIPTS / f"{size}-{condition}.jsonl"


def run_path(out: Path, size: str, condition: str) -> Path:
    return out / RUNS / f"{size}-{condition}.json"


def machine(cpuinfo: Path = Path("/proc/cpuinfo")) -> str:
    """The CPU compute seconds were measured on, as the operating system names it."""
    name = ""
    if cpuinfo.exists():
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name"):
                name = line.split(":", 1)[1].strip()
                break
    elif platform.system() == "Darwin":
        found = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        name = found.stdout.strip()
    return f"{name or 'unknown CPU'}, {platform.system()} {platform.machine()}"


def local_transcriber(size: WhisperSize, models: str | None = None) -> Transcriber:
    """faster-whisper at `size`, built as the transcriber process builds it: ffmpeg found,
    the model loaded and a generated clip transcribed, or CannotTranscribeError. Local mode
    is named here, so a FIRSTCALL_TRANSCRIBE_MODE of replay never stands in for Whisper.
    The models folder is FIRSTCALL_WHISPER_MODELS unless `models` is given."""
    settings = Settings(transcribe_mode="local", whisper_size=size)
    if models is not None:
        settings = settings.model_copy(update={"whisper_models": models})
    return from_settings(settings)


def _lines(rows: Iterable[Mapping[str, Any]]) -> str:
    return "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows)


def _partial(path: Path) -> Path:
    return path.with_name(f".{path.name}.partial")


def _write_new(path: Path, text: str) -> None:
    """Written whole or not at all, so a run killed midway leaves no half file to score."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _partial(path).write_text(text)
    os.replace(_partial(path), path)


def transcribed(out: Path, size: str, condition: str) -> bool:
    """Whether the pair is transcribed: its transcripts and run file both in place. A stop
    between the two moves of `transcribe_pair` left the run file beside its target, and it
    is moved in here, so the pair is never transcribed again."""
    transcripts, run = transcripts_path(out, size, condition), run_path(out, size, condition)
    if transcripts.exists() and not run.exists() and _partial(run).exists():
        os.replace(_partial(run), run)
    if transcripts.exists() != run.exists():
        raise TranscribedError(
            f"only one of {transcripts} and {run} exists, so the pair cannot be read or "
            "transcribed again"
        )
    return transcripts.exists()


def transcribe_pair(
    clips: Clips,
    size: WhisperSize,
    condition: str,
    out: Path,
    transcriber: Transcriber,
    timer: Callable[[], float] = time.perf_counter,
) -> list[dict[str, Any]]:
    """Every clip of one condition through one size, timed. Writes the transcripts and the
    pair's run file, and refuses if either exists. What Whisper heard that the product would
    refuse to make a note of (no speech, or too long) is kept as an empty transcript with the
    reason, scored as an answer that fills nothing. A clip that fails before Whisper hears it
    raises ClipFailedError and writes nothing."""
    transcripts, run = transcripts_path(out, size, condition), run_path(out, size, condition)
    for path in (transcripts, run):
        if path.exists():
            raise TranscribedError(f"{path} exists: each pair is transcribed once")
    rows: list[dict[str, Any]] = []
    for clip in clips.rows(condition):
        audio = (clips.folder / clip["file"]).read_bytes()
        fmt = Path(clip["file"]).suffix[1:]
        started = timer()
        try:
            heard = transcriber.transcribe(audio, fmt)
        except UnusableAudioError as failed:
            raise ClipFailedError(
                f"{clip['id']}: {failed}; nothing is written, so the pair can run again"
            ) from failed
        try:
            text, error = note_text(heard), None
        except UnusableAudioError as found:
            text, error = "", str(found)
        rows.append(
            {
                "id": clip["id"],
                "sha256": audio_key(audio),
                "text": text,
                "error": error,
                "compute_seconds": round(timer() - started, 3),
            }
        )
    meta = {
        "seed": clips.seed,
        "size": size,
        "condition": condition,
        "whisper_revision": WHISPER_REVISIONS[size],
        "machine": machine(),
        "record_passes": [],
    }
    for path, text in ((run, json.dumps(meta, indent=2, sort_keys=True) + "\n"),
                       (transcripts, _lines(rows))):  # fmt: skip
        path.parent.mkdir(parents=True, exist_ok=True)
        _partial(path).write_text(text)
    # The transcripts go in first; `transcribed` moves in a run file a stop left behind.
    os.replace(_partial(transcripts), transcripts)
    os.replace(_partial(run), run)
    return rows


def read_transcripts(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def compute(
    clip_rows: Sequence[Mapping[str, Any]], transcripts: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Transcription time per minute of audio, from the seconds each clip took and each
    clip's length."""
    audio = sum(row["seconds"] for row in clip_rows)
    spent = sum(row["compute_seconds"] for row in transcripts)
    return {
        "clips": len(transcripts),
        "audio_seconds": round(audio, 3),
        "compute_seconds": round(spent, 3),
        "per_minute": round(spent / audio * 60, 2) if audio else None,
    }


def _own_clips(
    clips: Clips, size: str, condition: str, transcripts: Sequence[Mapping[str, Any]]
) -> None:
    """Each transcript is of its own clip: one per clip, in the clip list's order, hashing
    the audio the manifest names for that clip. A clean and a degraded clip share a script,
    so only the hash tells one condition's transcripts from the other's."""
    if [row["id"] for row in transcripts] != [clip["id"] for clip in clips.rows(condition)]:
        raise ValueError(f"the {size} {condition} transcripts do not match the clips one for one")
    audio = clip_audio(clips.manifest)
    for row in transcripts:
        if audio.get((condition, row["id"])) != row["sha256"]:
            raise ValueError(
                f"the {size} {condition} transcript of {row['id']} is not of its clip's audio"
            )


def evaluate_pair(
    clips: Clips,
    size: str,
    condition: str,
    server_url: str,
    run: NoteRun,
    out: Path = VOICE_RUNS,
    on_read: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Read one pair's transcripts and score them. Transcripts that are not one for each of
    the pair's clips, of that clip's audio, raise before any read.

    `on_read` is given the call log and the ledger before the first read, after each read,
    and when a read is stopped, with how many record-mode answers came from recordings
    already on file ("reused", at no cost)."""
    from firstcall.seed import seed as seed_world

    clip_rows = clips.rows(condition)
    transcripts = read_transcripts(transcripts_path(out, size, condition))
    _own_clips(clips, size, condition, transcripts)
    ids = [row["id"] for row in clip_rows]
    scripts = {script["id"]: script for script in read_jsonl(clips.seed, SCRIPTS)}
    wanted = set(ids)
    labels = [label for label in read_jsonl(clips.seed, LABELS) if label["id"] in wanted]
    # Record mode sends this pair's transcripts and nothing else.
    texts = frozenset(sha256(row["text"].encode()) for row in transcripts if row["text"])
    synthetic = SyntheticIndex(texts) if run.mode == "record" else None
    answers: dict[str, dict[str, Any] | None] = {}
    unanswered: list[str] = []
    read: list[dict[str, Any]] = []
    with throwaway_database(server_url) as sessions:
        org = seed_world(sessions)
        gateway = _gateway(sessions, run, synthetic)
        reader = NoteReader(gateway, detached=True)
        on_file = gateway.recordings.for_task(READ_NOTE) if gateway.recordings else None
        reused = 0

        def report_progress() -> None:
            if on_read is not None:
                with sessions() as session:
                    on_read({**_calls(session), "reused": reused})

        report_progress()
        try:
            for row in transcripts:
                if not row["text"]:
                    answers[row["id"]] = None
                    continue
                note_date = scripts[row["id"]]["note_date"]
                read.append({"text": row["text"], "note_date": note_date})
                if run.mode == "record" and on_file is not None:
                    request = reader.request(org, None, row["text"], note_date, ZONE)
                    reused += on_file.find(gateway.outbound(request)) is not None
                outcome = reader.read(org, None, row["text"], note_date, ZONE)
                if outcome.answer is not None:
                    answers[row["id"]] = outcome.answer.as_json()
                elif outcome.reason == UNREADABLE:
                    answers[row["id"]] = None
                else:
                    unanswered.append(row["id"])
                report_progress()
        except BaseException:
            # Stopped mid-read, by an error or Ctrl-C: what the read reserved is on the ledger
            # and must be kept before the database goes.
            report_progress()
            raise
        with sessions() as session:
            calls = _calls(session)
        calls["json_prompt_calls"] = max(
            _json_prompt_answers(gateway, reader, org, read),
            int(getattr(gateway.transport, "json_prompt_calls", 0)),
        )
    heard = {row["id"]: row["text"] for row in transcripts}
    return {
        "seed": clips.seed,
        "size": size,
        "condition": condition,
        "model": run.model,
        "mode": run.mode,
        "prompt_sha256": prompt_sha256(),
        "complete": not unanswered,
        "unanswered": unanswered,
        "not_transcribed": [row["id"] for row in transcripts if not row["text"]],
        "score": voice_score.score([scripts[i] for i in ids], labels, answers),
        "wer": voice_score.wer((scripts[i]["text"], heard[i]) for i in ids),
        "compute": compute(clip_rows, transcripts),
        "calls": calls,
        "structured_output": calls["json_prompt_calls"] == 0,
    }


def _set_record_pass(path: Path, index: int, record_pass: dict[str, Any]) -> None:
    meta = json.loads(path.read_text())
    passes = meta["record_passes"]
    passes[index:] = [record_pass]
    _write_new(path, json.dumps(meta, indent=2, sort_keys=True) + "\n")


def record_pair(
    clips: Clips, size: str, condition: str, server_url: str, run: NoteRun, out: Path = VOICE_RUNS
) -> dict[str, Any]:
    """The paid path for one pair: read every transcript, keeping each answer, then score
    from the recordings. The record pass (calls, answers reused from recordings already on
    file, failed attempts, ledger spend) is in the pair's run file from before the first
    read and is brought up to date after each, so a pass that is stopped still shows what it
    spent; until it ends, its unanswered list is null. A pass that left transcripts
    unanswered can be run again: it pays only for the rest, and the report adds the passes
    up."""
    path = run_path(out, size, condition)
    index = len(json.loads(path.read_text())["record_passes"])
    latest: dict[str, Any] = {}

    def keep(progress: dict[str, Any]) -> None:
        latest.update(progress)
        _set_record_pass(path, index, {**progress, "model": run.model, "unanswered": None})

    recorded = evaluate_pair(clips, size, condition, server_url, run, out, on_read=keep)
    record_pass = {**recorded["calls"], "reused": latest["reused"], "model": run.model,
                   "unanswered": recorded["unanswered"]}  # fmt: skip
    _set_record_pass(path, index, record_pass)
    try:
        result = evaluate_pair(clips, size, condition, server_url, replace(run, mode="replay"), out)
    except ReplayMissError as error:
        raise IncompleteRecordingError(record_pass, str(error)) from error
    result["structured_output"] = result["structured_output"] and recorded["structured_output"]
    return result


def spent(record_passes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Every record pass of a pair added up: the answers paid for, those reused from
    recordings already on file, failed attempts, the ledger's spend, and how many passes
    were stopped before their end."""
    return {
        "passes": len(record_passes),
        "stopped": sum(p["unanswered"] is None for p in record_passes),
        "answers": sum(p["ok"] - p["reused"] for p in record_passes),
        "reused": sum(p["reused"] for p in record_passes),
        "failed": sum(p["failed"] for p in record_passes),
        "ledger_usd": f"{sum(Decimal(p['ledger_usd']) for p in record_passes):.6f}",
    }


def evaluate_voice(
    server_url: str,
    model: str,
    clips: Clips | None = None,
    out: Path = VOICE_RUNS,
    recordings: Path = RECORDINGS,
) -> dict[str, Any]:
    """Every pair with a run file, scored in replay mode, the Atlas timing, and the pick once
    every pair is in and complete."""
    clips = clips or Clips()
    run = NoteRun(mode="replay", model=model, budget_usd=Decimal(0), recordings=recordings)
    pairs: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for size in SIZES:
        for condition in CONDITIONS:
            path = run_path(out, size, condition)
            if not path.exists():
                missing.append(f"{size}-{condition}")
                continue
            meta = json.loads(path.read_text())
            result = evaluate_pair(clips, size, condition, server_url, run, out)
            result["whisper_revision"] = meta["whisper_revision"]
            result["machine"] = meta["machine"]
            result["record_pass"] = spent(meta["record_passes"])
            pairs[f"{size}-{condition}"] = result
    timing = out / ATLAS_TIMING
    report: dict[str, Any] = {
        "seed": clips.seed,
        "model": model,
        "prompt_sha256": prompt_sha256(),
        "settings": SETTINGS,
        "speed_bound": SPEED_BOUND,
        "voices": clips.voices(),
        "pairs": pairs,
        "missing": missing,
        "atlas": json.loads(timing.read_text()) if timing.exists() else None,
    }
    report["complete"] = not missing and all(pair["complete"] for pair in pairs.values())
    report["pick"] = pick(report) if report["complete"] and report["atlas"] else None
    return report


def degraded_floors(report: Mapping[str, Any], size: str) -> list[dict[str, Any]]:
    return report["pairs"][f"{size}-degraded"]["score"]["all"]["floors"]


def pick(report: Mapping[str, Any]) -> dict[str, Any]:
    """ADR-005's pick rule: the smallest size whose degraded run meets every notes floor and
    runs within the speed bound on Atlas. If none does, the largest size within the speed
    bound ships, with the floors it misses. Point estimates decide. If no size is within the
    bound, the rule names none."""
    per_minute = {size: report["atlas"]["sizes"][size]["per_minute"] for size in SIZES}
    within = [size for size in SIZES if per_minute[size] <= report["speed_bound"]]
    for size in within:
        if all(row["met"] for row in degraded_floors(report, size)):
            return {
                "size": size,
                "rule": "the smallest size that meets every floor within the speed bound",
                "misses": [],
            }
    if not within:
        return {"size": None, "rule": "no size runs within the speed bound", "misses": []}
    size = within[-1]
    misses = [row for row in degraded_floors(report, size) if not row["met"]]
    rule = "no size meets every floor, so the largest within the speed bound ships"
    return {"size": size, "rule": rule, "misses": misses}


def _cell(value: Any) -> str:
    return "n/a" if value is None else str(value)


def _interval(interval: Sequence[float] | None) -> str:
    return "" if interval is None else f"[{interval[0]}, {interval[1]}]"


def _intervals(part: Mapping[str, Any]) -> list[str]:
    """Each floor's 95% interval, in the order `voice_score.floors` lists the floors. The
    unsupported floor is a count of notes, so its interval is the Wilson interval of that
    count's share of the scripts, as a count of notes."""
    found = [_interval(part["fields"][name]["f1_interval"]) for name in BANT_FIELDS]
    found += [_interval(part[key]["interval"]) for key in ("key_terms", "authority_exact",
                                                           "month_exact")]  # fmt: skip
    share = wilson(part["unsupported"]["count"], part["scripts"])
    notes = None if share is None else [round(bound * part["scripts"], 1) for bound in share]
    return [*found, _interval(notes)]


def _rate(rate: Mapping[str, Any]) -> str:
    if not rate["total"]:
        return "n/a"
    return f"{rate['hits']}/{rate['total']} = {rate['rate']} {_interval(rate['interval'])}"


def _pair_lines(pair: Mapping[str, Any]) -> list[str]:
    whole = pair["score"]["all"]
    seen = voice_score.floors(pair["score"]["seen"])
    unseen = voice_score.floors(pair["score"]["unseen"])
    lines = [
        f"### {pair['size']}, {pair['condition']}",
        "",
        "| Measure | Floor | All | 95% interval | Seen | Unseen | Met |",
        "|---|---|---|---|---|---|---|",
    ]
    rows = zip(whole["floors"], _intervals(whole), seen, unseen, strict=True)
    for row, interval, seen_row, unseen_row in rows:
        lines.append(
            f"| {row['measure']} | {row['floor']} | {_cell(row['value'])} | {interval} | "
            f"{_cell(seen_row['value'])} | {_cell(unseen_row['value'])} | "
            f"{'yes' if row['met'] else 'no'} |"
        )
    strict = whole["strict"]
    lines += [
        f"| {strict['measure']} | {strict['floor']} | {strict['value']} |  | "
        f"{pair['score']['seen']['unsupported_strict']['count']} | "
        f"{pair['score']['unseen']['unsupported_strict']['count']} | |",
        "",
        f"Suggested contacts: precision {_rate(whole['actions']['precision'])}, recall "
        f"{_rate(whole['actions']['recall'])}.",
        f"Whisper `{pair['size']}` at revision `{pair['whisper_revision']}`, transcribed on "
        f"{pair['machine']}. Reads: {pair['calls']['calls']}, replayed from the recorded "
        f"answers, which cost ${pair['calls']['cost_usd']} when they were recorded.",
    ]
    paid = pair["record_pass"]
    stopped = f" {paid['stopped']} stopped before its end." if paid["stopped"] else ""
    lines.append(
        f"Record passes: {paid['passes']}, with {paid['answers']} answers paid for, "
        f"{paid['reused']} reused from earlier recordings at no cost and {paid['failed']} "
        f"failed attempts; ${paid['ledger_usd']} spent, by the ledger.{stopped}"
    )
    if not pair["structured_output"]:
        lines.append(
            f"**NOT on structured output: {pair['calls']['json_prompt_calls']} calls** fell back "
            "to JSON in the prompt."
        )
    return [*lines, ""]


def _markdown(report: Mapping[str, Any]) -> str:
    settings = report["settings"]
    voices = report["voices"]
    lines = [
        f"# Voice eval, seed {report['seed']}",
        "",
        f"Model `{report['model']}`, note prompt sha256 `{report['prompt_sha256']}`. Whisper on "
        f"the CPU: {settings['compute_type']}, {settings['cpu_threads']} threads, language "
        f"`{settings['language']}`, beam {settings['beam_size']}, the voice activity filter "
        "on, no conditioning on earlier text. Each transcript is read once by the note reader "
        "and scored against the script its voice read. Names count by sound "
        "(evals/gold/voice/name-spellings.json); numbers count as written.",
        "",
        "## Pick",
        "",
    ]
    chosen, atlas = report["pick"], report["atlas"]
    shipped = f"`{chosen['size']}`" if chosen["size"] else "none"
    lines += [
        f"**Shipped size: {shipped}.** ADR-005's rule: {chosen['rule']}.",
        "",
        f"| Size | Degraded run meets every floor | Atlas compute s per audio minute | "
        f"Within {report['speed_bound']} |",
        "|---|---|---|---|",
    ]
    for size in SIZES:
        met = all(row["met"] for row in degraded_floors(report, size))
        per_minute = atlas["sizes"][size]["per_minute"]
        lines.append(
            f"| {size} | {'yes' if met else 'no'} | {per_minute} | "
            f"{'yes' if per_minute <= report['speed_bound'] else 'no'} |"
        )
    lines += [
        "",
        f"Atlas: {atlas['host']}, the voice image limited to {atlas['cpus']} CPUs, "
        f"{atlas['clips']} {atlas['condition']} seed-{atlas['seed']} clips per size, "
        f"{atlas['date']}.",
    ]
    for miss in chosen["misses"]:
        lines.append(f"- Missed: {miss['measure']}, {miss['value']} against {miss['floor']}.")
    lines += [
        "",
        "## Every run",
        "",
        "| Size | Condition | Floors met | WER | Not transcribed | Unreadable answers | "
        "Compute s per audio minute where transcribed |",
        "|---|---|---|---|---|---|---|",
    ]
    for pair in report["pairs"].values():
        whole = pair["score"]["all"]
        met = sum(bool(row["met"]) for row in whole["floors"])
        lines.append(
            f"| {pair['size']} | {pair['condition']} | {met} of {len(whole['floors'])} | "
            f"{pair['wer']['wer']} ({pair['wer']['errors']} of {pair['wer']['words']} words) | "
            f"{len(pair['not_transcribed'])} | {whole['unreadable']} | "
            f"{pair['compute']['per_minute']} |"
        )
    lines += ["", "## Notes floors", ""]
    for pair in report["pairs"].values():
        lines += _pair_lines(pair)
    lines += ["## Voices", "", "| Voice | Scripts |", "|---|---|"]
    lines += [f"| `{voice}` | {count} |" for voice, count in voices.items()]
    return "\n".join(lines) + "\n"


def report_paths(seed: int, date: str, root: Path = REPORTS) -> tuple[Path, Path]:
    stem = root / f"{date}-voice-seed-{seed}"
    return stem.with_suffix(".json"), stem.with_suffix(".md")


def write_voice(report: dict[str, Any], date: str, root: Path = REPORTS) -> list[Path]:
    """The report, only once every pair is in, every transcript answered and the Atlas timing
    there to pick with."""
    if not report["complete"] or report["pick"] is None:
        gaps = [f"missing {', '.join(report['missing'])}"] if report["missing"] else []
        gaps += [f"{key} unanswered" for key, pair in report["pairs"].items()
                 if not pair["complete"]]  # fmt: skip
        if report["atlas"] is None:
            gaps.append(f"no {ATLAS_TIMING}")
        raise ValueError(f"the voice eval is incomplete ({'; '.join(gaps)}); no report is written")
    root.mkdir(parents=True, exist_ok=True)
    data, text = report_paths(report["seed"], date, root)
    data.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    text.write_text(_markdown(report))
    return [data, text]
