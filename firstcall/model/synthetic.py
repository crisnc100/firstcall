"""Which inputs came from the synthetic generator. Record mode sends only these to a model,
so no real person's message is ever recorded into the repository."""

from __future__ import annotations

import json
from pathlib import Path

from firstcall.core.delivery import sha256
from firstcall.corpus.generate import GOLD_DIR, TWINS


class SyntheticIndex:
    def __init__(self, hashes: frozenset[str]) -> None:
        self.hashes = hashes

    @classmethod
    def from_gold(cls, root: Path = GOLD_DIR) -> SyntheticIndex:
        """Every message body and clean twin of every committed seed."""
        hashes: set[str] = set()
        for folder in sorted(root.glob("seed-*")):
            for name in ("messages.jsonl", TWINS):
                path = folder / name
                if path.exists():
                    for line in path.read_text().splitlines():
                        hashes.add(sha256(json.loads(line)["body"].encode()))
        return cls(frozenset(hashes))

    def __contains__(self, raw: bytes) -> bool:
        return sha256(raw) in self.hashes
