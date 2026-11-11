"""Rerun every committed rules-only eval and require the same bytes.

    uv run python -m scripts.check_replay_report

Each report under evals/reports/ named <date>-rules-seed-<N>.json is rerun for its seed and
date on a throwaway database. Any difference in the JSON or the Markdown fails.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from firstcall.evaluation.report import REPORTS, paths, write
from firstcall.evaluation.run import evaluate
from firstcall.settings import Settings

NAME = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-rules-seed-(?P<seed>\d+)\.json$")


def committed(root: Path = REPORTS) -> list[tuple[str, int]]:
    found = [NAME.match(p.name) for p in sorted(root.glob("*-rules-seed-*.json"))]
    return [(m.group("date"), int(m.group("seed"))) for m in found if m]


def main() -> int:
    reports = committed()
    if not reports:
        print("FAIL no committed rules-only report to reproduce")
        return 1
    failures = 0
    url = Settings().database_url
    for date, seed in reports:
        with tempfile.TemporaryDirectory() as scratch:
            fresh = write(evaluate(seed, url), date, Path(scratch))
            for new, old in zip(fresh, paths(seed, date), strict=True):
                same = new.read_bytes() == old.read_bytes()
                print(f"{'ok' if same else 'DIFFERS'} {old.relative_to(REPORTS.parents[1])}")
                failures += not same
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
