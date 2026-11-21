"""Which inputs came from the synthetic generator. Record mode sends only these to a model,
so no real person's message is ever recorded into the repository."""

from __future__ import annotations

import json
from pathlib import Path

from firstcall.core.delivery import sha256
from firstcall.corpus.generate import GOLD_DIR, TWINS
from firstcall.corpus.voice import VOICE_MANIFEST

UNIT_NOTES = "unit-notes.jsonl"
# The voice eval's transcripts of the voice gold (firstcall/evaluation/voice_eval.py).
VOICE_TRANSCRIPTS = GOLD_DIR.parent / "voice" / "transcripts"


def _hashes(path: Path, key: str) -> set[str]:
    if not path.exists():
        return set()
    return {sha256(json.loads(line)[key].encode()) for line in path.read_text().splitlines()}


def clip_audio(manifest: Path) -> dict[tuple[str, str], str]:
    """The hash of each rendered clip's audio, by its condition and script id, from the clip
    lists beside the voice manifest. A clean and a degraded clip share a script id."""
    if not manifest.exists():
        return {}
    files: dict[str, str] = json.loads(manifest.read_text())["files"]
    audio: dict[tuple[str, str], str] = {}
    for clips in sorted(manifest.parent.glob("seed-*/clips.jsonl")):
        for row in map(json.loads, clips.read_text().splitlines()):
            digest = files.get(f"{clips.parent.name}/{row['file']}")
            if digest is not None:
                audio[(row["condition"], row["id"])] = digest
    return audio


def _transcripts(folder: Path, manifest: Path) -> set[str]:
    """Each transcript's text, taken only when its audio is its own clip's, as the voice
    manifest hashes it: <size>-<condition>.jsonl names the condition, the row its script. A
    transcript is Whisper's output, not the generator's, so the audio it came from is what
    shows it is synthetic."""
    if not folder.exists():
        return set()
    audio = clip_audio(manifest)
    found: set[str] = set()
    for path in sorted(folder.glob("*.jsonl")):
        condition = path.stem.rpartition("-")[2]
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row["text"] and audio.get((condition, row["id"])) == row["sha256"]:
                found.add(sha256(row["text"].encode()))
    return found


class SyntheticIndex:
    def __init__(self, hashes: frozenset[str]) -> None:
        self.hashes = hashes

    @classmethod
    def from_gold(
        cls,
        root: Path = GOLD_DIR,
        transcripts: Path = VOICE_TRANSCRIPTS,
        voice_manifest: Path = VOICE_MANIFEST,
    ) -> SyntheticIndex:
        """Every message body and clean twin of every committed seed, every note script, the
        fixed unit notes, and each transcript of its own clip in the voice manifest."""
        hashes: set[str] = set()
        for folder in sorted(root.glob("seed-*")):
            for name in ("messages.jsonl", TWINS):
                hashes |= _hashes(folder / name, "body")
        for folder in sorted((root / "notes").glob("seed-*")):
            hashes |= _hashes(folder / "scripts.jsonl", "text")
        hashes |= _hashes(root / UNIT_NOTES, "text")
        hashes |= _transcripts(transcripts, voice_manifest)
        return cls(frozenset(hashes))

    def __contains__(self, raw: bytes) -> bool:
        return sha256(raw) in self.hashes
