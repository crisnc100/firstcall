"""The eval command's model flags, without a key or a network."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from firstcall.cli import app
from scripts.check_replay_report import committed


@pytest.fixture(autouse=True)
def no_ambient_model_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("FIRSTCALL_MODEL_"):
            monkeypatch.delenv(name)


@pytest.mark.parametrize("mode", ["record", "live"])
def test_a_paid_mode_without_a_key_stops_before_any_run(mode: str) -> None:
    result = CliRunner().invoke(app, ["eval", "--seed", "7", "--mode", mode])
    assert result.exit_code != 0
    assert "FIRSTCALL_MODEL_KEY" in result.output


def test_a_run_budget_over_ten_dollars_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRSTCALL_MODEL_KEY", "not-a-real-key")
    result = CliRunner().invoke(
        app, ["eval", "--seed", "7", "--mode", "record", "--budget", "10.01"]
    )
    assert result.exit_code != 0
    assert "$10" in result.output


def test_an_unknown_mode_is_refused() -> None:
    result = CliRunner().invoke(app, ["eval", "--seed", "7", "--mode", "guess"])
    assert result.exit_code != 0


def test_the_replay_check_finds_every_kind_of_committed_report(tmp_path: Path) -> None:
    root = tmp_path
    for name in (
        "2026-11-11-rules-seed-7.json",
        "2026-11-12-model-seed-11.json",
        "2026-11-12-confirm-seed-13.json",
        "2026-11-12-model-seed-11.md",
        "notes.json",
    ):
        (root / name).write_text("{}")
    assert committed(root) == [
        ("confirm", "2026-11-12", 13),
        ("model", "2026-11-12", 11),
        ("rules", "2026-11-11", 7),
    ]
