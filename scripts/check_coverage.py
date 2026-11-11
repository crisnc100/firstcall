"""Per-module branch coverage floors.

    uv run python -m scripts.check_coverage coverage.json

Reads coverage.py's JSON report (collected with --cov-branch) and fails when a listed module
is below its floor. A listed module missing from the report fails too: an unmeasured floor
is not met.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

CORE = 95.0
EDGE = 85.0
FLOORS: dict[str, float] = {
    "firstcall/core/delivery.py": CORE,
    "firstcall/api.py": EDGE,
    "firstcall/intake/auth.py": EDGE,
    "firstcall/intake/limits.py": EDGE,
    "firstcall/intake/store.py": EDGE,
}


def check(report: dict[str, Any], floors: dict[str, float]) -> list[str]:
    if report.get("meta", {}).get("branch_coverage") is not True:
        return ["the report was not collected with branch coverage"]
    files: dict[str, Any] = report.get("files", {})
    failures: list[str] = []
    for module, floor in floors.items():
        entry = files.get(module)
        if entry is None:
            failures.append(f"{module}: not in the coverage report")
            continue
        summary = entry["summary"]
        total = int(summary.get("num_branches", 0))
        covered = int(summary.get("covered_branches", 0))
        percent = 100.0 if total == 0 else 100.0 * covered / total
        status = "ok" if percent >= floor else "BELOW FLOOR"
        print(
            f"{module}: {covered}/{total} branches = {percent:.1f}% (floor {floor:.0f}%) {status}"
        )
        if percent < floor:
            failures.append(f"{module}: {percent:.1f}% < {floor:.0f}%")
    return failures


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "coverage.json")
    failures = check(json.loads(path.read_text()), FLOORS)
    for failure in failures:
        print(f"FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
