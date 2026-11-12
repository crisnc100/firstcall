"""The committed model choices follow their rules: the ADR-002 pick on the seed-7 pass report
(F13) and config/field_methods.yaml on the seed-11 report (F14)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from firstcall.evaluation.model_eval import choose_methods, pick
from firstcall.evaluation.model_report import model_paths
from firstcall.evaluation.run import FIELD_METHODS
from firstcall.pipeline.model_read import load_field_methods

ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs" / "adr" / "ADR-002-model-candidates.md"
DATE = "2026-11-12"


def _report(seed: int) -> dict[str, Any]:
    return json.loads(model_paths(seed, DATE)[0].read_text())


def test_the_pick_rule_applied_to_the_pass_report_gives_the_chosen_model() -> None:
    passes = [_report(7)["pass"]]
    assert [p["model"] for p in passes] == ["claude-haiku-5-5"]
    assert pick(passes) == "claude-haiku-5-5"
    assert "**Pick:** claude-haiku-5-5" in ADR.read_text()


def test_field_methods_match_the_decision_rule_on_the_seed_11_report() -> None:
    report = _report(11)
    assert report["decision"] == choose_methods(report)
    data = yaml.safe_load(FIELD_METHODS.read_text())
    assert data["source"] == f"evals/reports/{model_paths(11, DATE)[0].name}"
    assert data["methods"] == report["decision"]
    assert dict(load_field_methods()) == report["decision"]
