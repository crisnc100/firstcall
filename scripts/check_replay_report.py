"""Rerun every committed eval report and require the same bytes.

    uv run python -m scripts.check_replay_report

Each report under evals/reports/ named <date>-<kind>-seed-<N>.json is rerun for its seed and
date on a throwaway database: rules reports with no model, model, confirm and notes reports
from the recorded answers in evals/recordings/ (replay mode, no key). A missing recording
raises. Any difference in the JSON or the Markdown fails. A notes report keeps its paid record
pass, which a replay cannot know, so that part is carried over from the committed report. The
voice report needs no carrying over and no Whisper: its transcripts, compute seconds and
record passes are committed under evals/voice/.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from firstcall.evaluation.model_eval import ModelRun, evaluate_confirm, evaluate_models
from firstcall.evaluation.model_report import confirm_paths, model_paths, write_confirm, write_model
from firstcall.evaluation.note_eval import NoteRun, evaluate_notes, write_notes
from firstcall.evaluation.report import REPORTS, paths, write
from firstcall.evaluation.run import evaluate
from firstcall.evaluation.voice_eval import evaluate_voice, report_paths, write_voice
from firstcall.settings import Settings

NAME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<kind>rules|model|confirm|notes|voice)-seed-(?P<seed>\d+)\.json$"
)


def committed(root: Path = REPORTS) -> list[tuple[str, str, int]]:
    found = [NAME.match(p.name) for p in sorted(root.glob("*-seed-*.json"))]
    rows = [(m.group("kind"), m.group("date"), int(m.group("seed"))) for m in found if m]
    return sorted(rows)


def rerun(kind: str, date: str, seed: int, url: str, scratch: Path) -> tuple[Path, Path]:
    """The report written again from the same inputs: rules-only, or recorded answers."""
    if kind == "rules":
        return write(evaluate(seed, url), date, scratch)
    old = json.loads(paths_for(kind, date, seed)[0].read_text())
    if kind == "notes":
        report = evaluate_notes(
            seed, url, NoteRun(mode="replay", model=old["model"], budget_usd=Decimal("0"))
        )
        for paid in ("record_pass", "structured_output"):
            if paid in old:
                report[paid] = old[paid]
        data, text = write_notes(report, date, scratch)
        return data, text
    if kind == "voice":
        data, text = write_voice(evaluate_voice(url, old["model"]), date, scratch)
        return data, text
    run = ModelRun(mode="replay", model=old["model"], budget_usd=Decimal("0"))
    if kind == "model":
        return write_model(evaluate_models(seed, url, run), date, scratch)
    return write_confirm(evaluate_confirm(seed, url, run, old["field_methods"]), date, scratch)


def paths_for(kind: str, date: str, seed: int) -> tuple[Path, Path]:
    if kind == "rules":
        return paths(seed, date)
    if kind == "model":
        return model_paths(seed, date)
    if kind == "notes":
        stem = REPORTS / f"{date}-notes-seed-{seed}"
        return stem.with_suffix(".json"), stem.with_suffix(".md")
    if kind == "voice":
        return report_paths(seed, date)
    return confirm_paths(seed, date)


def main() -> int:
    reports = committed()
    if not reports:
        print("FAIL no committed report to reproduce")
        return 1
    failures = 0
    url = Settings().database_url
    for kind, date, seed in reports:
        with tempfile.TemporaryDirectory() as scratch:
            fresh = rerun(kind, date, seed, url, Path(scratch))
            for new, old in zip(fresh, paths_for(kind, date, seed), strict=True):
                same = new.read_bytes() == old.read_bytes()
                print(f"{'ok' if same else 'DIFFERS'} {old.relative_to(REPORTS.parents[1])}")
                failures += not same
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
