"""Voice memos for the voice eval: seed 11's note scripts as speech (ADR-005).

`firstcall corpus voice --piper CMD --models DIR` writes evals/gold/voice/seed-11: each script
once clean and once degraded, both in one voice, as 16 kHz mono MP3, plus the spoken text,
the clip list and the voice manifest. No real person is recorded.

Piper (GPL-3.0-or-later) renders the speech. It runs as a separate program and is never
imported, installed or locked here; only the audio it made is committed. ffmpeg does the
filtering and encoding. Piper's output may differ between runs or machines, which is why the
audio itself is the gold and the manifest holds its hashes.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from firstcall.corpus.generate import GOLD_DIR
from firstcall.corpus.notes import NOTE_SEEDS, NUMBER_WORDS, SCRIPTS, read_jsonl

VOICE_DIR = GOLD_DIR / "voice"
VOICE_SEED = 11
CLIPS = "clips.jsonl"
SPOKEN = "spoken.jsonl"
CONDITIONS = ("clean", "degraded")
VOICE_MANIFEST = VOICE_DIR / "MANIFEST.json"

SAMPLE_RATE = 16_000
MP3_BITRATE = "24k"
# A degraded clip: Piper's speed changed by SPEED_CHANGE either way, then white noise at
# SNR_DB, the phone band and an Opus round trip.
SPEED_CHANGE = 0.10
SNR_DB = 10
PHONE_BAND = (300, 3400)
OPUS_BITRATE = "12k"


@dataclass(frozen=True)
class Voice:
    model: str
    # A speaker of a multi-speaker model, by its name in the model's speaker map.
    speaker: str | None = None

    @property
    def id(self) -> str:
        return f"{self.model}:{self.speaker}" if self.speaker else self.model


# Ten voices, 15 scripts each. Every one is licensed for reuse of its output, as
# evals/gold/voice/LICENSES.md records.
VOICES = (
    Voice("en_US-joe-medium"),
    Voice("en_US-kathleen-medium"),
    Voice("en_US-kristin-medium"),
    Voice("en_US-mike-medium"),
    Voice("en_US-ljspeech-high"),
    Voice("en_GB-cori-high"),
    Voice("en_GB-alba-medium"),
    Voice("en_GB-vctk-medium", "p245"),
    Voice("en_GB-vctk-medium", "p326"),
    Voice("en_GB-vctk-medium", "p237"),
)

# A figure as the note scripts write it: "$425k", "785K", "$450,000", "450 thousand",
# "450 grand", "$1.2 million", "$1.25M".
FIGURE = re.compile(
    r"(?P<dollar>\$)?(?P<whole>\d{1,3}(?:,\d{3})+|\d+)(?P<fraction>\.\d+)?"
    r"(?:\s*(?P<scale>k|K|M|million|thousand|grand)\b)?"
)
FIGURE_SCALES = {
    "k": 1_000, "K": 1_000, "thousand": 1_000, "grand": 1_000, "M": 1_000_000, "million": 1_000_000,
}  # fmt: skip


def _under_thousand(n: int) -> str:
    parts: list[str] = []
    if n >= 100:
        parts.append(f"{NUMBER_WORDS[n // 100]} hundred")
        n %= 100
    if n >= 20:
        tens, ones = n - n % 10, n % 10
        parts.append(NUMBER_WORDS[tens] + (f"-{NUMBER_WORDS[ones]}" if ones else ""))
    elif n:
        parts.append(NUMBER_WORDS[n])
    return " ".join(parts)


def spoken_number(value: int) -> str:
    """A whole number in words, in the form the note scorer's number reader reads back:
    1200000 is "one million two hundred thousand", never "one point two million"."""
    if value <= 0:
        raise ValueError("spoken numbers are positive")
    words: list[str] = []
    for size, name in ((1_000_000_000, "billion"), (1_000_000, "million"), (1_000, "thousand")):
        count, value = divmod(value, size)
        if count:
            words.append(f"{_under_thousand(count)} {name}")
    if value:
        words.append(_under_thousand(value))
    return " ".join(words)


def _say_figure(match: re.Match[str]) -> str:
    value = Decimal(match["whole"].replace(",", "") + (match["fraction"] or ""))
    value *= FIGURE_SCALES[match["scale"]] if match["scale"] else 1
    if value != value.to_integral_value():
        raise ValueError(f"not a whole figure: {match[0]}")
    words = spoken_number(int(value))
    return f"{words} dollars" if match["dollar"] else words


def spoken_text(text: str) -> str:
    """What the voice says: the script with each figure written out as its full value in
    words, and "dollars" where it had "$". The rest of the script is unchanged."""
    return FIGURE.sub(_say_figure, text)


def plan_clips(seed: int = VOICE_SEED) -> list[dict[str, Any]]:
    """Each script's voice and its degraded clip's speed and noise, before any audio exists.
    Every voice reads the same number of scripts; a script keeps its voice in both clips."""
    if seed not in NOTE_SEEDS:
        raise ValueError(f"no note scripts for seed {seed}")
    rng = random.Random(f"voice-{seed}")
    ids = [script["id"] for script in read_jsonl(seed, SCRIPTS)]
    if len(ids) % len(VOICES):
        raise ValueError("the voices do not divide the scripts evenly")
    voices = [voice for voice in VOICES for _ in range(len(ids) // len(VOICES))]
    rng.shuffle(voices)
    rows: list[dict[str, Any]] = []
    for script_id, voice in zip(ids, voices, strict=True):
        speed = 1 + rng.choice((-SPEED_CHANGE, SPEED_CHANGE))
        noise_seed = rng.randrange(2**32)
        for condition in CONDITIONS:
            degraded = condition == "degraded"
            rows.append(
                {
                    "id": script_id,
                    "condition": condition,
                    "file": f"{condition}/{script_id}.mp3",
                    "voice": voice.id,
                    "speed": round(speed, 2) if degraded else 1.0,
                    "noise_seed": noise_seed if degraded else None,
                }
            )
    return rows


def spoken_rows(seed: int = VOICE_SEED) -> list[dict[str, str]]:
    return [{"id": s["id"], "text": spoken_text(s["text"])} for s in read_jsonl(seed, SCRIPTS)]


def _lines(rows: Sequence[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows)


def voice_file_hashes(root: Path = VOICE_DIR) -> dict[str, str]:
    """Every gold file under the voice folder but the manifest, by its path there."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != root / VOICE_MANIFEST.name
    }


def read_voice_manifest(path: Path = VOICE_MANIFEST) -> dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())["files"]


def write_voice_manifest(root: Path = VOICE_DIR) -> dict[str, str]:
    hashes = voice_file_hashes(root)
    body = {"files": hashes}
    (root / VOICE_MANIFEST.name).write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    return hashes


def _add_noise(source: Path, target: Path, seed: int) -> None:
    """White noise at SNR_DB below the clip's own level, drawn from a seeded generator. The
    mix is scaled down only if it would clip."""
    with wave.open(str(source), "rb") as reader:
        if reader.getsampwidth() != 2 or reader.getnchannels() != 1:
            raise ValueError(f"{source}: expected 16-bit mono from Piper")
        rate = reader.getframerate()
        samples = array("h", reader.readframes(reader.getnframes()))
    if sys.byteorder == "big":
        samples.byteswap()
    level = (sum(s * s for s in samples) / max(len(samples), 1)) ** 0.5
    sigma = level / 10 ** (SNR_DB / 20)
    rng = random.Random(seed)
    mixed = [s + rng.gauss(0.0, sigma) for s in samples]
    peak = max((abs(m) for m in mixed), default=0.0)
    gain = min(1.0, 32767 / peak) if peak else 1.0
    out = array("h", (round(m * gain) for m in mixed))
    if sys.byteorder == "big":
        out.byteswap()
    with wave.open(str(target), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(out.tobytes())


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args], check=True
    )


def _to_mp3(source: Path, target: Path) -> None:
    # bitexact keeps the encoder's version string out of the file.
    _ffmpeg("-i", str(source), "-map_metadata", "-1", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-c:a", "libmp3lame", "-b:a", MP3_BITRATE, "-fflags", "+bitexact",
            "-flags:a", "+bitexact", str(target))  # fmt: skip


def seconds(path: Path) -> float:
    found = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return round(float(found.stdout.strip()), 3)


def _speaker_number(models: Path, voice: Voice) -> str:
    config = json.loads((models / f"{voice.model}.onnx.json").read_text())
    return str(config["speaker_id_map"][voice.speaker])


def _piper(
    piper: Sequence[str], models: Path, voice: Voice, speed: float, text: str, target: Path
) -> None:
    command = [*piper, "-m", str(models / f"{voice.model}.onnx"), "-f", str(target),
               "--length-scale", f"{1 / speed:.4f}"]  # fmt: skip
    if voice.speaker:
        command += ["-s", _speaker_number(models, voice)]
    subprocess.run(command, input=text, text=True, check=True, capture_output=True)
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError(f"Piper wrote no audio for {voice.id}")


def render_clip(
    piper: Sequence[str], models: Path, clip: dict[str, Any], text: str, root: Path
) -> None:
    """One clip: Piper at the clip's speed, then for a degraded clip noise, the phone band
    and an Opus round trip, then MP3."""
    voice = next(v for v in VOICES if v.id == clip["voice"])
    target = root / clip["file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        _piper(piper, models, voice, clip["speed"], text, work / "speech.wav")
        if clip["condition"] == "clean":
            _to_mp3(work / "speech.wav", target)
            return
        _add_noise(work / "speech.wav", work / "noisy.wav", clip["noise_seed"])
        low, high = PHONE_BAND
        _ffmpeg("-i", str(work / "noisy.wav"), "-af", f"highpass=f={low},lowpass=f={high}",
                "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "libopus", "-b:a", OPUS_BITRATE,
                str(work / "phone.ogg"))  # fmt: skip
        _to_mp3(work / "phone.ogg", target)


def render_voice(
    piper: Sequence[str], models: Path, root: Path = VOICE_DIR, seed: int = VOICE_SEED
) -> dict[str, str]:
    """Every clip of the seed, its spoken text and clip list, and the voice manifest. The
    LICENSES.md and name-spellings.json files beside it are written by hand.

    The seed is rendered into a folder of its own beside the old one and swapped in only once
    every clip is made, so a failed render leaves the gold as it was. A run killed during the
    swap leaves the manifest out of step with the folder, which the gold test reports."""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"{tool} is needed to render the voice gold")
    folder = root / f"seed-{seed}"
    spoken = spoken_rows(seed)
    texts = {row["id"]: row["text"] for row in spoken}
    clips = plan_clips(seed)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root, prefix=f".rendering-seed-{seed}-") as scratch:
        stage = Path(scratch) / folder.name
        stage.mkdir()
        for clip in clips:
            render_clip(piper, models, clip, texts[clip["id"]], stage)
            clip["seconds"] = seconds(stage / clip["file"])
        (stage / SPOKEN).write_text(_lines(spoken))
        (stage / CLIPS).write_text(_lines(clips))
        if folder.exists():
            folder.rename(Path(scratch) / "replaced")
        stage.rename(folder)
    return write_voice_manifest(root)
