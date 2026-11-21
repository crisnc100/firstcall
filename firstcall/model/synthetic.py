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


def _transcripts(folder: Path, manifest: Path) -> set[str]:
    """Each transcript's text, taken only when its audio is a clip the voice manifest lists.
    A transcript is Whisper's output, not the generator's, so the audio it came from is what
    shows it is synthetic."""
    if not folder.exists() or not manifest.exists():
        return set()
    files: dict[str, str] = json.loads(manifest.read_text())["files"]
    clips = {digest for name, digest in files.items() if name.endswith(".mp3")}
    found: set[str] = set()
    for path in sorted(folder.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row["sha256"] in clips and row["text"]:
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
        fixed unit notes, and the transcripts of clips the voice manifest lists."""
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
