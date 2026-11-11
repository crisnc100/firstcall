"""The synthetic corpus: minimum counts, seen and unseen layouts, the manifest, and the order
in which gold and predictions were committed."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from firstcall.corpus.generate import GOLD_DIR, SEEDS, generate
from firstcall.corpus.manifest import file_hashes, read_manifest

ROOT = Path(__file__).resolve().parents[1]
UNSEEN_LAYOUTS = {
    "portal-gablework",
    "portal-lattice",
    "forwarded-gablework",
    "forwarded-lattice",
    "partner-form-v2",
}
HARD_NEGATIVE_KINDS = {
    "shared_agent_phone",
    "household_phone",
    "portal_relay_sender",
    "call_tracking_number",
    "phone_email_conflict",
}
EXTRACTION_CODE = ("firstcall/core/read.py", "firstcall/core/match.py", "firstcall/core/place.py")
PREDICTIONS = (
    "docs/adr/ADR-001-rules-or-model-per-field.md",
    "docs/adr/ADR-002-model-candidates.md",
    "docs/adr/ADR-003-matching.md",
    "docs/adr/ADR-004-lead-events-outbox.md",
    "docs/adr/ADR-005-local-transcription.md",
    "docs/adr/ADR-006-no-model-fallback.md",
)


@cache
def labels(seed: int) -> list[dict[str, Any]]:
    text = (GOLD_DIR / f"seed-{seed}" / "labels.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines()]


@cache
def messages(seed: int) -> list[dict[str, Any]]:
    text = (GOLD_DIR / f"seed-{seed}" / "messages.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines()]


def test_the_manifest_matches_every_committed_gold_file() -> None:
    manifest = read_manifest()
    assert sorted(manifest, key=int) == [str(s) for s in SEEDS]
    for seed in SEEDS:
        assert manifest[str(seed)] == file_hashes(seed)
        assert set(manifest[str(seed)]) == {"messages.jsonl", "labels.jsonl"}


@pytest.mark.parametrize("seed", SEEDS)
def test_the_generator_reproduces_the_committed_gold(seed: int) -> None:
    generated_messages, generated_labels = generate(seed)
    folder = GOLD_DIR / f"seed-{seed}"
    assert generated_messages == (folder / "messages.jsonl").read_text()
    assert generated_labels == (folder / "labels.jsonl").read_text()


@pytest.mark.parametrize("seed", SEEDS)
def test_each_seed_meets_every_minimum_count(seed: int) -> None:
    rows = labels(seed)
    assert len(rows) == 500
    assert [m["id"] for m in messages(seed)] == [r["id"] for r in rows]
    category = Counter(r["category"] for r in rows)
    case = Counter(r["case"] for r in rows)
    assert category["lead"] >= 250
    assert case["repeat"] >= 60
    assert case["agent"] >= 40
    assert case["forwarded"] >= 40
    assert category["spam"] >= 50
    assert category["vendor"] >= 40
    assert category["support"] >= 40
    spam_forms = Counter(r["layout"] for r in rows if r["case"] == "spam_form")
    assert sum(spam_forms.values()) >= 20
    assert spam_forms["spam-form-honeypot"] >= 1
    assert spam_forms["spam-form"] >= 1
    injected = [r for r in rows if r["injection"]]
    assert len(injected) >= 30
    assert {r["category"] for r in injected} == {"lead", "spam", "vendor", "support"}
    hard = Counter(kind for r in rows for kind in r["hard_negatives"])
    assert set(hard) == HARD_NEGATIVE_KINDS
    assert all(hard[kind] >= 20 for kind in HARD_NEGATIVE_KINDS), hard
    assert case["ambiguous"] >= 30


@pytest.mark.parametrize("seed", SEEDS)
def test_every_repeat_points_to_an_earlier_lead(seed: int) -> None:
    first_seen: set[str] = set()
    for row in labels(seed):
        if row["case"] == "repeat":
            assert row["lead"] in first_seen
        elif row["lead"]:
            assert row["lead"] not in first_seen
            first_seen.add(row["lead"])


def test_unseen_layouts_appear_only_in_seeds_11_and_13() -> None:
    seed_7 = {r["layout"] for r in labels(7)}
    assert not seed_7 & UNSEEN_LAYOUTS
    assert all(r["seen"] for r in labels(7))
    for seed in (11, 13):
        rows = labels(seed)
        unseen = [r for r in rows if not r["seen"]]
        assert len(unseen) >= 60
        assert {r["layout"] for r in unseen} == UNSEEN_LAYOUTS
        assert all((r["layout"] in UNSEEN_LAYOUTS) != r["seen"] for r in rows)
        seen_portals = {r["layout"] for r in rows if r["layout"].startswith("portal-")}
        assert len(seen_portals - UNSEEN_LAYOUTS) >= 5
    form_shapes = {
        r["layout"] for r in labels(7) if r["layout"] in ("site-form", "partner-form-v1")
    }
    assert len(form_shapes) == 2


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _first_commit_adding(path: str) -> str | None:
    found = _git("log", "--diff-filter=A", "--format=%H", "--", path).splitlines()
    return found[-1] if found else None


def test_gold_and_predictions_were_committed_before_any_extraction_code() -> None:
    assert _git("rev-parse", "--is-shallow-repository") == "false", "needs full history"
    gold = ["evals/gold/MANIFEST.json"] + [
        f"evals/gold/seed-{s}/{n}" for s in SEEDS for n in ("messages.jsonl", "labels.jsonl")
    ]
    earlier: dict[str, str | None] = {p: _first_commit_adding(p) for p in (*gold, *PREDICTIONS)}
    for code in EXTRACTION_CODE:
        if not (ROOT / code).exists():
            continue
        code_commit = _first_commit_adding(code)
        for path, commit in earlier.items():
            assert commit is not None, f"{path} is not committed, but {code} exists"
            if code_commit is None:
                continue
            assert commit != code_commit, f"{path} landed together with {code}"
            ancestor = subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit, code_commit], cwd=ROOT
            )
            assert ancestor.returncode == 0, f"{path} was committed after {code}"


@pytest.mark.parametrize("seed", SEEDS)
def test_every_gold_value_is_present_in_its_message(seed: int) -> None:
    """Gold never claims a contact, name or item that the message does not contain."""
    problems: list[str] = []
    for message, label in zip(messages(seed), labels(seed), strict=True):
        body = message["body"]
        folded = body.casefold()
        digits = "".join(ch for ch in body if ch.isdigit())
        if label["phone"] and label["phone"][2:] not in digits:
            problems.append(f"{label['id']} phone")
        if label["email"] and label["email"] not in folded:
            problems.append(f"{label['id']} email")
        if label["name"] and any(w not in folded for w in label["name"].casefold().split()):
            problems.append(f"{label['id']} name")
        if label["item"] and label["item"].casefold() not in folded:
            problems.append(f"{label['id']} item")
    assert problems == []
