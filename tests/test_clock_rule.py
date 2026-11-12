"""Core code reads time only through an injected Clock."""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "firstcall" / "core"
FORBIDDEN = {
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("datetime", "today"),
    ("date", "today"),
    ("time", "time"),
    ("time", "monotonic"),
}


def direct_clock_reads(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute):
            owner = node.value
            name = owner.id if isinstance(owner, ast.Name) else getattr(owner, "attr", "")
            if (name, node.attr) in FORBIDDEN:
                found.append(f"{name}.{node.attr} at line {node.lineno}")
        if isinstance(node, ast.ImportFrom) and node.module in {"time"}:
            found.extend(f"from time import {a.name}" for a in node.names)
    return found


def test_no_direct_clock_reads_in_core() -> None:
    files = sorted(CORE.rglob("*.py"))
    assert files
    problems = {
        str(f.relative_to(CORE)): hits for f in files if (hits := direct_clock_reads(f.read_text()))
    }
    assert problems == {}


def test_the_check_catches_each_form() -> None:
    assert direct_clock_reads("import datetime\ndatetime.now()") == ["datetime.now at line 2"]
    assert direct_clock_reads("x = date.today()") == ["date.today at line 1"]
    assert direct_clock_reads("from time import time") == ["from time import time"]
    assert direct_clock_reads("datetime.datetime.now()") == ["datetime.now at line 1"]
    assert direct_clock_reads("clock.now()") == []
