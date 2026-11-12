"""evals/gold/MANIFEST.json: the SHA-256 of every committed gold file, checked by a test."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from firstcall.corpus.generate import GOLD_DIR

MANIFEST = GOLD_DIR / "MANIFEST.json"


def file_hashes(seed: int, root: Path = GOLD_DIR) -> dict[str, str]:
    folder = root / f"seed-{seed}"
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(folder.glob("*.jsonl"))
    }


def read_manifest(path: Path = MANIFEST) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())["seeds"]


def record_seed(seed: int, hashes: dict[str, str], path: Path = MANIFEST) -> None:
    seeds = read_manifest(path)
    seeds[str(seed)] = hashes
    body = {"seeds": dict(sorted(seeds.items(), key=lambda kv: int(kv[0])))}
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
