"""The rules-only eval: intervals, scoring rules, floors, the seed-13 guard, and the committed
reports (F5, F12)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from firstcall.evaluation import run
from firstcall.evaluation.metrics import Rate, wilson
from firstcall.evaluation.report import REPORTS, floor_results, markdown, paths, with_floors
from firstcall.evaluation.run import FIELDS, Result, score


def test_wilson_intervals_match_known_values() -> None:
    assert wilson(0, 0) is None
    low, high = wilson(0, 10) or (0, 0)
    assert low == 0.0
    assert high == pytest.approx(0.2775, abs=1e-4)
    low, high = wilson(5, 10) or (0, 0)
    assert (low, high) == (pytest.approx(0.2366, abs=1e-4), pytest.approx(0.7634, abs=1e-4))
    assert Rate(0, 0).text() == "n/a (0 cases)"
    assert Rate(1, 2).as_json()["rate"] == 0.5


def label(id_: str, **values: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": id_,
        "category": "lead",
        "name": "Ana Ruiz",
        "phone": "+12025550142",
        "email": "ana@inbox.example",
        "item": "Copperleaf",
        "agent": None,
        "team": "west",
        "lead": "L1",
        "case": "new",
        "layout": "site-form",
        "seen": True,
        "hard_negatives": [],
        "injection": None,
    }
    return {**base, **values}


def result(id_: str, lead: str | None, created: bool, **values: Any) -> Result:
    extracted = {
        "name": "Ana Ruiz",
        "phone": "+12025550142",
        "email": "ana@inbox.example",
        "item": "Copperleaf",
        "agent": None,
        **values.pop("extracted", {}),
    }
    status = "lead_created" if created else ("attached" if lead else "not_a_lead")
    return Result(
        id_,
        values.pop("status", status),
        "why",
        values.pop("category", "lead"),
        extracted,
        values.pop("team", "west"),
        lead,
        created,
    )


def test_scoring_counts_attaches_merges_injections_and_leaks() -> None:
    agent = {"name": "Dana", "email": "dana@x.example", "phone": "+12025550101"}
    labels = [
        label("m1"),
        label("m2", case="repeat"),
        label("m3", lead="L2", name="Bea Cole", case="repeat"),
        label("m4", lead="L3", hard_negatives=["household_phone", "call_tracking_number"]),
        label("m5", lead="L4", agent=agent, phone=None),
        label(
            "m6",
            category="spam",
            lead=None,
            team=None,
            case="spam",
            injection={"field": "category", "value": "lead"},
        ),
        label("m7", lead="L5", team=None, case="ambiguous", seen=False),
        label("m8", lead="L2", name="Bea Cole", case="new"),
    ]
    results = [
        result("m1", "S1", True),
        result("m2", "S1", False),
        result("m3", "S1", False),
        result("m4", "S1", False),
        result("m5", "S5", True, extracted={"phone": "+12025550101"}),
        result("m6", "S6", True, team=None),
        result("m7", "S7", True, team="north"),
        result("m8", "S8", True),
    ]
    seen = score(results, labels)["splits"]["seen"]
    assert seen["matching"]["recall"]["hits"] == 1
    assert seen["matching"]["wrong_attaches"] == {"count": 1, "of": 2}
    assert seen["matching"]["hard_negatives"]["household_phone"] == {"wrong_merges": 1, "of": 1}
    assert seen["matching"]["hard_negative_total"] == {"wrong_merges": 1, "of": 1}
    assert seen["injection"]["taken"] == 1
    assert seen["agent_contact_leak"] == {"count": 1, "of": 1}
    assert seen["fields"]["phone"]["hits"] == 5
    assert seen["category"]["spam_as_lead"]["hits"] == 1
    assert seen["category"]["lead_as_not_lead"] == 0
    unseen = score(results, labels)["splits"]["unseen"]
    assert unseen["placement"]["ambiguous_placed"] == {"count": 1, "of": 1}
    assert set(seen["fields"]) == set(FIELDS)


def test_injection_on_a_contact_field_counts_only_the_attacker_value() -> None:
    labels = [
        label("m1", injection={"field": "name", "value": "Admin Override"}),
        label("m2", lead="L2", injection={"field": "team", "value": "north"}),
    ]
    results = [
        result("m1", "S1", True, extracted={"name": "admin  override"}),
        result("m2", "S2", True),
    ]
    assert score(results, labels)["splits"]["all"]["injection"]["by_field"] == {
        "name": {"taken": 1, "attacker_value_in_output": 1, "of": 1},
        "team": {"taken": 0, "attacker_value_in_output": 0, "of": 1},
    }


def test_floors_read_seen_layouts_and_render() -> None:
    labels = [label("m1"), label("m2", case="repeat")]
    report = score([result("m1", "S1", True), result("m2", "S1", False)], labels)
    report.update(seed=7, method="rules-only", model_calls=0)
    floors = {row["measure"]: row for row in floor_results(report)}
    assert floors["Wrong attaches (repeat messages)"]["met"] is True
    assert floors["Spam classed as lead"]["met"] is None
    text = markdown(with_floors(report), "2026-01-01")
    assert "| Matching | Wrong attaches (repeat messages) | Safety | 0 | 0 | yes |" in text
    assert "| Category | Spam classed as lead | Quality | <= 10% of spam | n/a | n/a |" in text


def test_the_confirm_seed_is_refused_until_the_field_methods_are_committed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(run, "FIELD_METHODS", tmp_path / "field_methods.yaml")
    with pytest.raises(RuntimeError, match="confirm run"):
        run.evaluate(13, "postgresql+psycopg://unused/x")


def committed(seed: int) -> tuple[dict[str, Any], str]:
    json_path, md_path = paths(seed, "2026-11-11")
    return json.loads(json_path.read_text()), md_path.read_text()


@pytest.mark.parametrize("seed", [7, 11])
def test_each_committed_report_covers_every_field_with_intervals_split_by_layout(seed: int) -> None:
    report, text = committed(seed)
    assert report["seed"] == seed
    assert report["model_calls"] == 0
    assert set(report["splits"]) == {"seen", "unseen", "all"}
    for split in ("seen", "all"):
        for field in FIELDS:
            entry = report["splits"][split]["fields"][field]
            assert entry["total"] > 0
            assert len(entry["interval"]) == 2
    assert markdown(report, "2026-11-11") == text


def test_the_held_out_report_has_unseen_layouts_and_every_message_terminal() -> None:
    report, _ = committed(11)
    unseen = report["splits"]["unseen"]
    assert unseen["messages"] >= 60
    assert all(unseen["fields"][f]["interval"] for f in FIELDS)
    everything = report["splits"]["all"]
    assert everything["messages"] == 500
    assert everything["not_terminal"] == 0
    assert everything["missing_reason"] == 0


def _git(*args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", *args], cwd=REPORTS.parents[1], capture_output=True, text=True, check=True
    ).stdout


def _is_ancestor(older: str, newer: str) -> bool:
    import subprocess

    found = subprocess.run(
        ["git", "merge-base", "--is-ancestor", older, newer], cwd=REPORTS.parents[1]
    )
    return found.returncode == 0


def _squash_merge_on_main(commit: str) -> bool:
    """A PR squashed onto main: one parent, a subject ending "(#N)", reachable from main.
    CI proved the methods-then-report order on the PR branch before it was squashed."""
    import subprocess

    subject, parents = _git("log", "-1", "--format=%s%n%P", commit).splitlines()
    if not re.search(r"\(#\d+\)$", subject) or len(parents.split()) != 1:
        return False
    for ref in ("origin/main", "main"):
        known = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=REPORTS.parents[1],
            capture_output=True,
        )
        if known.returncode == 0 and _is_ancestor(commit, ref):
            return True
    return False


def _results(committed_json: str) -> dict[str, Any]:
    report = json.loads(committed_json)
    report.pop("field_methods_sha256", None)
    return report


def test_the_only_seed_13_run_is_one_confirm_run_committed_after_the_field_methods() -> None:
    import hashlib

    methods_path = run.FIELD_METHODS.relative_to(REPORTS.parents[1]).as_posix()
    reports = REPORTS.relative_to(REPORTS.parents[1]).as_posix()

    # One run: exactly the JSON and Markdown pair of one dated confirm report.
    seed_13 = sorted(p.name for p in REPORTS.glob("*seed-13*"))
    assert len(seed_13) == 2, seed_13
    stem = seed_13[0].removesuffix(".json")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-confirm-seed-13", stem), seed_13
    assert seed_13 == [f"{stem}.json", f"{stem}.md"]
    report_json, report_md = f"{reports}/{stem}.json", f"{reports}/{stem}.md"

    # The report ran with the methods file as committed.
    committed_methods = _git("show", f"HEAD:{methods_path}").encode()
    report = json.loads((REPORTS / f"{stem}.json").read_text())
    assert report["field_methods_sha256"] == hashlib.sha256(committed_methods).hexdigest()

    # Each file of the pair was added once, in the same commit.
    [added] = _git("log", "--diff-filter=A", "--format=%H", "--", report_json).split()
    assert _git("log", "--diff-filter=A", "--format=%H", "--", report_md).split() == [added]

    # Never rewritten: a later version may add the methods hash, but no result changes.
    versions = _git("log", "--format=%H", "--", report_json).split()
    first = _results(_git("show", f"{added}:{report_json}"))
    for version in versions:
        assert _results(_git("show", f"{version}:{report_json}")) == first, version

    # The methods were committed before the report and never changed after it. They may
    # first appear in the report's own commit only when that commit is a PR squashed onto
    # main, whose branch history CI already checked.
    touched = _git("log", "--format=%H", "--", methods_path).split()
    assert touched, f"{methods_path} is not committed"
    for commit in touched:
        if commit == added:
            assert _squash_merge_on_main(added), (
                f"{methods_path} and {stem} share {added[:7]}, not a squash merge on main"
            )
        else:
            assert _is_ancestor(commit, added), (
                f"{methods_path} changed in {commit[:7]}, after or beside the confirm report"
            )


def test_an_injection_counts_only_when_the_clean_twin_differs() -> None:
    labels = [
        label(
            "m1",
            category="vendor",
            lead=None,
            team=None,
            case="vendor",
            injection={"field": "category", "value": "lead"},
        ),
        label(
            "m2",
            category="spam",
            lead=None,
            team=None,
            case="spam",
            injection={"field": "category", "value": "lead"},
        ),
        label("m3", lead="L3", injection={"field": "phone", "value": "+13055550199"}),
    ]
    same = result("t1", None, False)
    caused = result("t2", None, False, category="spam", status="not_a_lead")
    results = [
        Result(**{**result("m1", "S1", True).__dict__, "twin": same}),
        Result(**{**result("m2", "S2", True).__dict__, "twin": caused}),
        Result(
            **{
                **result("m3", "S3", True, extracted={"phone": "+13055550199"}).__dict__,
                "twin": result("t3", None, False),
            }
        ),
    ]
    injection = score(results, labels)["splits"]["all"]["injection"]
    assert injection["taken"] == 2
    assert injection["attacker_value_in_output"] == 3
    assert injection["by_field"]["category"] == {
        "taken": 1,
        "attacker_value_in_output": 2,
        "of": 2,
    }
    assert injection["by_field"]["phone"] == {"taken": 1, "attacker_value_in_output": 1, "of": 1}
