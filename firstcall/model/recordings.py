"""Recorded model answers: written in record mode, read back in replay mode.

An answer is keyed by everything that shapes it: the model, the system prompt, the user
message and the answer schema. A changed prompt therefore misses in replay and fails loudly
instead of reusing an answer to a different question.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from firstcall.model.transport import TransportRequest


@dataclass(frozen=True)
class Recording:
    key: str
    model: str
    content: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: str
    latency_ms: int

    @property
    def cost(self) -> Decimal:
        return Decimal(self.cost_usd)


def request_key(request: TransportRequest) -> str:
    shape = {
        "model": request.model,
        "system": request.system,
        "user": request.user,
        "schema": request.schema,
    }
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()


class Recordings:
    """One JSONL file per model under a folder."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._loaded: dict[str, dict[str, Recording]] = {}

    def path(self, model: str) -> Path:
        return self.folder / f"{model.replace('/', '__')}.jsonl"

    def _for(self, model: str) -> dict[str, Recording]:
        if model not in self._loaded:
            path = self.path(model)
            rows: dict[str, Recording] = {}
            if path.exists():
                for line in path.read_text().splitlines():
                    data: dict[str, Any] = json.loads(line)
                    rows[data["key"]] = Recording(**data)
            self._loaded[model] = rows
        return self._loaded[model]

    def find(self, request: TransportRequest) -> Recording | None:
        return self._for(request.model).get(request_key(request))

    def save(self, recording: Recording) -> None:
        rows = self._for(recording.model)
        if recording.key in rows:
            return
        rows[recording.key] = recording
        self.folder.mkdir(parents=True, exist_ok=True)
        with self.path(recording.model).open("a") as handle:
            handle.write(json.dumps(asdict(recording), sort_keys=True) + "\n")
