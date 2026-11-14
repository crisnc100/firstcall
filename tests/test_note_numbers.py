"""The note scorer's number reading against real gold scripts: faithful rewrites state
nothing unsupported, invented numbers are flagged, and script and answer are read alike."""

from __future__ import annotations

from decimal import Decimal
from functools import cache
from typing import Any

import pytest

from firstcall.corpus.notes import LABELS, NOTE_SEEDS, SCRIPTS, read_jsonl
from firstcall.evaluation.note_score import read_numbers, unsupported


@cache
def _scripts() -> dict[str, str]:
    return {row["id"]: row["text"] for seed in NOTE_SEEDS for row in read_jsonl(seed, SCRIPTS)}


def answer(notes: str = "", **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "clean_note": "", "budget": None, "authority": None, "need": None, "timeline": None,
        "notes": notes, "actions": [],
    }  # fmt: skip
    return {**base, **fields}


def budget(text: str, low: int | None, high: int | None) -> dict[str, Any]:
    return {"text": text, "min": low, "max": high}


# How a careful reader might restate each script: other number styles, ranges written
# short, words as digits and digits as words. Every number in them is in the script.
FAITHFUL: list[tuple[str, dict[str, Any]]] = [
    (
        "note-7-002",
        answer(
            "Sell first. $880k\u2013$930k. 4BR, walkable street. WFH.",
            budget=budget("$880-930k", 880000, 930000),
        ),
    ),
    (
        "note-7-006",
        answer(
            "Cash buyer, ~$515K, moving within 5 months. Hannah decides.",
            budget=budget("about 515 thousand", 515000, 515000),
        ),
    ),
    ("note-7-007", answer("Around $795,000; move by December; 5 bedrooms, first-floor primary.")),
    ("note-7-008", answer("Minimum budget: 680 thousand.", budget=budget("", 680000, None))),
    ("note-7-011", answer("3BR + pool. Must sell first. Parents decide. At least $840,000.")),
    (
        "note-7-016",
        answer(
            "Budget 430 to 505 thousand. Parents decide. Pre-approved.",
            budget=budget("430-505k", 430000, 505000),
        ),
    ),
    (
        "note-7-028",
        answer("Cash. $350\u2013375k.", budget=budget("350 to 375 grand", 350000, 375000)),
    ),
    (
        "note-7-033",
        answer(
            "Five bedrooms and a pool; parents decide; cash; lease ends in "
            "January; budget ~$1,250,000; corner lot."
        ),
    ),
    (
        "note-7-041",
        answer(
            "Between 1.5 and 1.6 million. Decides alone. 3 beds + pool.",
            budget=budget("$1.5-1.6M", 1500000, 1600000),
        ),
    ),
    ("note-7-145", answer("Budget $330k to $430k, no rush.")),
    (
        "note-11-002",
        answer(
            "Between 425 and 450 thousand. No rush.",
            budget=budget("425 to 450 thousand", 425000, 450000),
        ),
    ),
    ("note-11-004", answer("Min $785,000. Parents decide; must sell first. Two-bed plus office.")),
    ("note-11-006", answer("Cash, ~345 thousand, walkable street.")),
    ("note-11-007", answer("Ceiling $1,200,000. Cash. Move by October.")),
    ("note-11-012", answer("At least $810k; decides alone; five bedrooms, garage; corner lot.")),
    (
        "note-11-014",
        answer(
            "$1.25 to 1.275 million budget. Husband signs off.",
            budget=budget("1.25-1.275M", 1250000, 1275000),
        ),
    ),
    ("note-11-021", answer("About $550k, move by August, pre-approved.")),
    ("note-11-028", answer("Budget about $945,000; pre-approved; move by November.")),
    (
        "note-11-037",
        answer(
            "Up to one million. Cash. Room for a dog.",
            budget=budget("one million max", None, 1000000),
        ),
    ),
    ("note-11-047", answer("Up to $865,000. 2 bedrooms plus a big yard.")),
    ("note-11-050", answer("Budget 705 to 730 grand; current place sells in October.")),
    ("note-11-062", answer("Between $395k and $445k. Move by March.")),
    (
        "note-11-066",
        answer(
            "Range: 815\u2013915 thousand. Big yard. Pre-approved.",
            budget=budget("815 to 915k", 815000, 915000),
        ),
    ),
    ("note-11-091", answer("Budget $440,000 to $465,000. Wife signs off.")),
    ("note-11-124", answer("1.1\u20131.125 million budget, move by September.")),
]

# Rewrites that invent a number: a wrong scale, wrong digits, or a number from nowhere.
INVENTED: list[tuple[str, dict[str, Any], list[str]]] = [
    ("note-11-002", answer("Between 425 and 450 million."),
     ["notes: number 4.25E+8", "notes: number 4.5E+8"]),
    ("note-7-006", answer("About $51,500."), ["notes: number 5.15E+4"]),
    ("note-7-007", answer("Around 795 thousand, 6 bedrooms."), ["notes: number 6"]),
    ("note-11-037", answer("Up to $1.5M."), ["notes: number 1.5E+6"]),
    ("note-7-041", answer("Between 1.5 and 1.6 billion."),
     ["notes: number 1.5E+9", "notes: number 1.6E+9"]),
    ("note-11-047", answer(budget=budget("", None, 86500)), ["budget.max: number 8.65E+4"]),
    ("note-7-145", answer("Budget $330k to $430k, 2 kids."), ["notes: number 2"]),
    ("note-11-050", answer("705 to 730 thousand, sells in 2027."), ["notes: number 2027"]),
    ("note-7-016", answer("430 to 550 thousand."), ["notes: number 5.5E+5"]),
    ("note-11-066", answer("nine hundred thirty thousand; three bedrooms"),
     ["notes: number 3", "notes: number 9.3E+5"]),
]  # fmt: skip


def test_the_table_covers_both_seeds_ranges_and_written_numbers() -> None:
    ids = {script_id for script_id, _ in FAITHFUL}
    assert len(ids) == len(FAITHFUL) >= 20
    assert {script_id.split("-")[1] for script_id in ids} == {str(s) for s in NOTE_SEEDS}
    labels = {r["id"]: r for seed in NOTE_SEEDS for r in read_jsonl(seed, LABELS)}
    ranges = [i for i in ids if (b := labels[i]["budget"]) and b["min"] and b["max"]
              and b["min"] != b["max"]]  # fmt: skip
    worded = [i for i in ids if "style-words" in labels[i]["phrasings"]]
    assert len(ranges) >= 10
    assert len(worded) >= 3
    assert len(INVENTED) >= 10


@pytest.mark.parametrize(("script_id", "given"), FAITHFUL, ids=[i for i, _ in FAITHFUL])
def test_a_faithful_rewrite_states_nothing_unsupported(
    script_id: str, given: dict[str, Any]
) -> None:
    assert unsupported(_scripts()[script_id], given) == []


@pytest.mark.parametrize(
    ("script_id", "given", "flags"), INVENTED, ids=[f"{i}-{n}" for n, (i, _, _) in
                                                    enumerate(INVENTED)]
)  # fmt: skip
def test_an_invented_number_is_flagged(
    script_id: str, given: dict[str, Any], flags: list[str]
) -> None:
    assert unsupported(_scripts()[script_id], given) == flags


@pytest.mark.parametrize("seed", NOTE_SEEDS)
def test_every_script_copied_through_or_restated_from_its_gold_is_supported(seed: int) -> None:
    """Every script, with a range, number words or several numbers in a sentence or not:
    copied word for word, and its budget restated in figures from the gold."""
    problems: list[str] = []
    for script, label in zip(read_jsonl(seed, SCRIPTS), read_jsonl(seed, LABELS), strict=True):
        text = script["text"]
        copied = unsupported(text, answer(clean_note=text, notes=text))
        restated: list[str] = []
        if label["budget"]:
            low, high = label["budget"]["min"], label["budget"]["max"]
            words = " to ".join(f"${v:,}" for v in (low, high) if v is not None)
            restated = unsupported(text, answer(budget=budget(words, low, high)))
        problems += [f"{script['id']} {flag}" for flag in copied + restated]
    assert problems == []


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("nine hundred thirty thousand; three bedrooms", {930000, 3}),
        ("two, three hundred", {2, 300}),
        ("Budget 450. Five kids.", {450, 5}),
        ("forty thousand \u2014 two dogs", {40000, 2}),
        ("Note: 12 calls", {12}),
        ("between 425 and 450 thousand", {425000, 450000}),
        ("$1.5M, 450,000 and 3.5 acres", {1500000, 450000, Decimal("3.5")}),
        ("2,000 to 3 million", {2000, 3000000}),
    ],
)
def test_a_number_never_runs_across_punctuation(text: str, values: set[int | Decimal]) -> None:
    assert read_numbers(text) == {Decimal(v).normalize() for v in values}
