"""The note scripts: counts, fill rates, held-out phrasing, gold that the script supports, and
gold committed before any note reader code."""

from __future__ import annotations

import re
from collections import Counter
from functools import cache
from typing import Any

import pytest

from firstcall.core.note_terms import ACTION_KINDS, AUTHORITIES, NON_ACTION_KINDS
from firstcall.corpus.notes import (
    ACTIONS,
    AUTHORITY,
    BUDGET_AROUND,
    BUDGET_AT_LEAST,
    BUDGET_BETWEEN,
    BUDGET_UP_TO,
    LABELS,
    MONTH_NAMES,
    NEED,
    NON_ACTIONS,
    NOTE_SEEDS,
    NUMBER_WORDS,
    SCRIPTS,
    TIMELINE,
    Phrasing,
    generate_notes,
    notes_file_hashes,
    read_jsonl,
    read_notes_manifest,
)
from firstcall.evaluation.note_score import read_numbers
from tests.test_corpus import _first_commit_adding, _git, _history_of

ROOT_FILES = (
    "evals/gold/notes/MANIFEST.json",
    *(f"evals/gold/notes/seed-{s}/{n}" for s in NOTE_SEEDS for n in (SCRIPTS, LABELS)),
    "docs/adr/ADR-007-note-reader.md",
    "firstcall/core/note_terms.py",
    "firstcall/evaluation/note_score.py",
)
# Where PR3b puts the note reader. Nothing here may exist before the gold and scorer.
READER_CODE = ("firstcall/notes/reader.py",)


@cache
def scripts(seed: int) -> list[dict[str, Any]]:
    return read_jsonl(seed, SCRIPTS)


@cache
def labels(seed: int) -> list[dict[str, Any]]:
    return read_jsonl(seed, LABELS)


def _norm(text: str) -> str:
    return " ".join(text.casefold().replace("-", " ").split())


def test_the_notes_manifest_matches_every_committed_file() -> None:
    manifest = read_notes_manifest()
    assert sorted(manifest, key=int) == [str(s) for s in NOTE_SEEDS]
    for seed in NOTE_SEEDS:
        assert manifest[str(seed)] == notes_file_hashes(seed)
        assert set(manifest[str(seed)]) == {SCRIPTS, LABELS}


@pytest.mark.parametrize("seed", NOTE_SEEDS)
def test_the_generator_reproduces_the_committed_scripts(seed: int) -> None:
    from firstcall.corpus.notes import NOTES_DIR

    generated_scripts, generated_labels = generate_notes(seed)
    folder = NOTES_DIR / f"seed-{seed}"
    assert generated_scripts == (folder / SCRIPTS).read_text()
    assert generated_labels == (folder / LABELS).read_text()


@pytest.mark.parametrize("seed", NOTE_SEEDS)
def test_each_seed_has_the_planned_mix(seed: int) -> None:
    rows = labels(seed)
    assert len(rows) == 150
    assert [s["id"] for s in scripts(seed)] == [r["id"] for r in rows]
    kinds = Counter(r["kind"] for r in rows)
    assert kinds["action"] == 60
    assert all(kinds[k] == 15 for k in NON_ACTION_KINDS), kinds
    assert kinds["neither"] == 15
    for row in rows:
        if row["kind"] == "action":
            assert 1 <= len(row["actions"]) <= 2
        else:
            assert row["actions"] == []
    assert sum(len(r["actions"]) == 2 for r in rows) == 15


@pytest.mark.parametrize("seed", NOTE_SEEDS)
def test_each_bant_field_is_empty_in_35_to_50_percent(seed: int) -> None:
    rows = labels(seed)
    for name in ("budget", "need", "timeline"):
        empty = sum(r[name] is None for r in rows) / len(rows)
        assert 0.35 <= empty <= 0.50, (name, empty)
    empty = sum(r["authority"] == "unknown" for r in rows) / len(rows)
    assert 0.35 <= empty <= 0.50


@pytest.mark.parametrize("seed", NOTE_SEEDS)
def test_gold_uses_only_the_adr_007_vocabulary(seed: int) -> None:
    for row in labels(seed):
        assert row["authority"] in AUTHORITIES
        assert set(row["actions"]) <= set(ACTION_KINDS)
        assert row["kind"] in {"action", "neither", *NON_ACTION_KINDS}
        if row["timeline"] and row["timeline"]["month"]:
            assert re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", row["timeline"]["month"])
        if row["budget"]:
            assert row["budget"]["min"] is not None or row["budget"]["max"] is not None


@pytest.mark.parametrize("seed", NOTE_SEEDS)
def test_every_gold_value_is_in_its_script(seed: int) -> None:
    problems: list[str] = []
    for script, row in zip(scripts(seed), labels(seed), strict=True):
        text = script["text"]
        if row["buyer"] is not None and row["buyer"] not in text:
            problems.append(f"{row['id']} buyer")
        for name, terms in row["terms"].items():
            problems += [f"{row['id']} {name} {t}" for t in terms if _norm(t) not in _norm(text)]
        if row["need"] and not row["terms"]["need"]:
            problems.append(f"{row['id']} need without a term")
        if row["budget"]:
            numbers = read_numbers(text)
            for bound in ("min", "max"):
                value = row["budget"][bound]
                if value is not None and value not in numbers:
                    problems.append(f"{row['id']} budget {bound}")
        if row["timeline"] and row["timeline"]["month"]:
            year, month = (int(p) for p in row["timeline"]["month"].split("-"))
            ahead = (year * 12 + month) - (int(row["note_date"][:4]) * 12
                                           + int(row["note_date"][5:7]))  # fmt: skip
            named = MONTH_NAMES[month - 1] in text
            counted = ahead in NUMBER_WORDS and f"within {NUMBER_WORDS[ahead]} months" in text
            if not (1 <= ahead <= 11 and (named or counted)):
                problems.append(f"{row['id']} month")
    assert problems == []


def _all_phrasings() -> list[Phrasing]:
    found: list[Phrasing] = []
    for group in (*ACTIONS.values(), *NON_ACTIONS.values()):
        found += group
    for group in (BUDGET_AROUND, BUDGET_UP_TO, BUDGET_AT_LEAST, BUDGET_BETWEEN, NEED):
        found += group
    found += [p for options in AUTHORITY.values() for p, _ in options]
    found += [p for p, _, _ in TIMELINE]
    return found


def test_unseen_phrasings_appear_only_in_seed_11() -> None:
    unseen = {p.id for p in _all_phrasings() if not p.seen}
    seed_7 = labels(7)
    assert all(r["seen"] for r in seed_7)
    assert not {pid for r in seed_7 for pid in r["phrasings"]} & unseen
    held_out = labels(11)
    share = sum(not r["seen"] for r in held_out) / len(held_out)
    assert share >= 0.40, share


def test_no_unseen_wording_appears_in_seed_7() -> None:
    """Each unseen phrasing's fixed words, slots aside, are absent from every seed-7 script."""
    seed_7 = " ".join(s["text"] for s in scripts(7))
    for phrasing in _all_phrasings():
        if phrasing.seen:
            continue
        pieces = [p.strip(" .,:") for p in re.split(r"\{[^}]+\}", phrasing.text)]
        longest = max(pieces, key=len)
        assert longest, phrasing.id
        assert longest not in seed_7, phrasing.id


def test_note_gold_and_the_scorer_were_committed_before_any_reader_code() -> None:
    """Checked from git history, squash merges included, as the message gold is."""
    assert _git("rev-parse", "--is-shallow-repository") == "false", "needs full history"
    earlier = {path: _first_commit_adding(path) for path in ROOT_FILES}
    for code in READER_CODE:
        code_commit = _first_commit_adding(code)
        if code_commit is None:
            continue
        for path, commit in earlier.items():
            assert commit is not None, f"{path} is not committed, but {code} is"
            first, later = commit, code_commit
            if first == later:
                ref = _history_of(first)
                assert ref != "HEAD", f"{path} landed together with {code}"
                first = _first_commit_adding(path, ref) or ""
                later = _first_commit_adding(code, ref) or ""
            assert first != later, f"{path} landed together with {code}"
            assert _git("merge-base", first, later) == first, f"{path} came after {code}"
