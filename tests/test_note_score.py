"""The frozen note scorer: number and name reading, value-aware fields, key terms, copies,
baselines and the pass rule."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from firstcall.corpus.notes import LABELS, NOTE_SEEDS, SCRIPTS, read_jsonl
from firstcall.evaluation.note_score import (
    all_met,
    baselines,
    copied,
    floors,
    read_numbers,
    score,
    score_part,
    unsupported,
)


def answer(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "clean_note": "",
        "budget": None,
        "authority": None,
        "need": None,
        "timeline": None,
        "notes": None,
        "actions": [],
    }
    return {**base, **fields}


def label(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "n1",
        "note_date": "2026-04-10",
        "kind": "neither",
        "actions": [],
        "budget": None,
        "authority": "unknown",
        "need": None,
        "timeline": None,
        "terms": {"budget": [], "authority": [], "need": [], "timeline": []},
        "seen": True,
    }
    return {**base, **fields}


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("forty thousand", {40000}),
        ("1.5 million", {1500000}),
        ("$400k and $40k", {400000, 40000}),
        ("$450,000", {450000}),
        ("three hundred seventy-five thousand", {375000}),
        ("four hundred and fifty", {450}),
        ("half a million", {500000}),
        ("a hundred", {100}),
        ("450 grand", {450000}),
        ("$1.25M", {1250000}),
        ("450K", {450000}),
        ("the 3rd", {3}),
        ("between four hundred thousand and five hundred five thousand", {400000, 505000}),
        ("Budget: SUV, HOA", set()),
    ],
)
def test_numbers_are_read_as_values(text: str, values: set[int]) -> None:
    assert read_numbers(text) == {Decimal(v).normalize() for v in values}


def test_the_month_field_is_never_an_unsupported_number() -> None:
    script = "Wants to move by March."
    assert unsupported(script, answer(timeline={"text": "by March", "month": "2027-03"})) == []


def test_a_number_written_another_way_is_supported() -> None:
    assert unsupported("Budget is $40,000.", answer(need="forty thousand budget")) == []
    assert unsupported("Can go to 1.5 million.", answer(notes="$1,500,000 max")) == []
    assert unsupported("Between 380 and 420 thousand.", answer(notes="$380,000")) == []


def test_a_changed_figure_or_an_added_year_is_unsupported() -> None:
    assert unsupported("Budget is $40k.", answer(notes="Budget $400k.")) == ["notes: number 4E+5"]
    found = unsupported("Move by March.", answer(timeline={"text": "March 2027", "month": None}))
    assert found == ["timeline.text: number 2027"]
    assert unsupported("About $40k.", answer(budget={"text": "", "min": 400000, "max": None}))


def test_labels_and_acronyms_are_not_names() -> None:
    script = "Needs room for the SUV and a low HOA fee."
    assert unsupported(script, answer(notes="Budget: unknown. SUV parking. HOA low.")) == []


def test_a_name_the_note_never_gave_is_unsupported() -> None:
    script = "Called Maria about the garage."
    assert unsupported(script, answer(notes="Maria wants a garage.")) == []
    assert unsupported(script, answer(notes="Maria and Gregory want a garage.")) == [
        "notes: name Gregory"
    ]


def _one(ans: dict[str, Any] | None, lab: dict[str, Any], text: str) -> dict[str, Any]:
    return score_part({"n1": text}, [lab], {"n1": ans})


def test_a_wrong_budget_counts_as_a_false_fill_and_a_miss() -> None:
    lab = label(budget={"min": 40000, "max": 40000})
    part = _one(answer(budget={"text": "", "min": 400000, "max": 400000}), lab, "About $40k.")
    assert {k: part["fields"]["budget"][k] for k in ("tp", "fp", "fn")} == {
        "tp": 0,
        "fp": 1,
        "fn": 1,
    }
    part = _one(answer(budget={"text": "", "min": 40000, "max": 40000}), lab, "About $40k.")
    assert part["fields"]["budget"]["tp"] == 1


def test_authority_unknown_is_empty_and_a_wrong_value_is_wrong() -> None:
    lab = label(authority="shares_decision")
    empty = _one(answer(authority={"value": "unknown", "others": ""}), lab, "x")
    assert empty["fields"]["authority"]["fn"] == 1
    assert empty["fields"]["authority"]["fp"] == 0
    wrong = _one(answer(authority={"value": "decision_maker", "others": ""}), lab, "x")
    assert (wrong["fields"]["authority"]["fp"], wrong["fields"]["authority"]["fn"]) == (1, 1)
    assert wrong["authority_exact"]["hits"] == 0


def test_a_timeline_needs_the_gold_month() -> None:
    lab = label(timeline={"month": "2026-06"})
    text = "Called Ana this morning. Wants to move by June. Has two dogs."
    good = _one(answer(timeline={"text": "by June", "month": "2026-06"}), lab, text)
    bad = _one(answer(timeline={"text": "by June", "month": "2027-06"}), lab, text)
    assert good["fields"]["timeline"]["tp"] == 1
    assert bad["fields"]["timeline"]["tp"] == 0
    assert bad["month_exact"]["hits"] == 0


def test_key_terms_count_only_in_their_own_field_and_never_in_a_copy() -> None:
    text = "Wants a garage and a pool. Must be near the lake with a dock and a view."
    lab = label(need=True, terms={"budget": [], "authority": [], "need": ["garage"],
                                  "timeline": []})  # fmt: skip
    assert _one(answer(need="a garage"), lab, text)["key_terms"]["hits"] == 1
    assert _one(answer(notes="a garage"), lab, text)["key_terms"]["hits"] == 0
    assert (
        _one(answer(timeline={"text": "garage", "month": None}), lab, text)["key_terms"]["hits"]
        == 0
    )
    assert copied(text, text)
    part = _one(answer(need=text), lab, text)
    assert part["key_terms"]["hits"] == 0
    assert part["fields"]["need"]["tp"] == 0
    assert part["copied_fields"] == 1


def test_an_unreadable_answer_is_scored_as_empty_and_counted() -> None:
    lab = label(need=True, terms={"budget": [], "authority": [], "need": ["garage"],
                                  "timeline": []})  # fmt: skip
    part = _one(None, lab, "Wants a garage.")
    assert part["unreadable"] == 1
    assert part["scripts"] == 1
    assert part["fields"]["need"]["fn"] == 1


def test_actions_are_scored_by_kind_and_non_actions_by_silence() -> None:
    labs = [
        label(id="a", kind="action", actions=["call"]),
        label(id="b", kind="future"),
    ]
    answers = {
        "a": answer(actions=[{"kind": "call", "quote": ""}, {"kind": "fax", "quote": ""}]),
        "b": answer(actions=[{"kind": "email", "quote": ""}]),
    }
    part = score_part({"a": "x", "b": "y"}, labs, answers)
    assert part["actions"]["precision"]["hits"] == 1
    assert part["actions"]["precision"]["total"] == 2
    assert part["actions"]["recall"]["rate"] == 1.0
    assert part["non_actions_without_suggestion"]["future"]["hits"] == 0


def test_a_floor_is_met_by_its_point_estimate() -> None:
    lab = label(need=True, terms={"budget": [], "authority": [], "need": ["garage"],
                                  "timeline": []})  # fmt: skip
    part = _one(answer(need="garage"), lab, "Wants a garage.")
    rows = {row["measure"]: row for row in floors(part)}
    assert rows["need F1"]["met"] is True
    assert rows["budget F1"]["met"] is None
    assert rows["notes with an unsupported number or name"]["met"] is True


@pytest.mark.parametrize("seed", NOTE_SEEDS)
def test_every_baseline_misses_and_the_gold_itself_passes(seed: int) -> None:
    scripts, labels = read_jsonl(seed, SCRIPTS), read_jsonl(seed, LABELS)
    found = baselines(scripts, labels)
    for name in ("always_fill", "never_fill", "copy_through"):
        assert not all_met(score(scripts, labels, found[name])["all"]), name
    gold = score(scripts, labels, found["gold"])
    assert all_met(gold["all"])
    assert gold["all"]["unsupported"]["count"] == 0
    assert gold["seen"]["scripts"] + gold["unseen"]["scripts"] == 150


def test_the_f1_interval_is_fixed_and_holds_the_estimate() -> None:
    scripts, labels = read_jsonl(7, SCRIPTS), read_jsonl(7, LABELS)
    answers = baselines(scripts, labels)["gold"]
    for key in list(answers)[::3]:
        answers[key] = None
    first = score(scripts, labels, answers)["all"]["fields"]["need"]
    again = score(scripts, labels, answers)["all"]["fields"]["need"]
    assert first["f1_interval"] == again["f1_interval"]
    low, high = first["f1_interval"]
    assert low <= first["f1"] <= high
