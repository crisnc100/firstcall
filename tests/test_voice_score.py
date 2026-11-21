"""The voice scorer: word error rate, and names judged by sound for voice only, with the strict
count beside the floor."""

from __future__ import annotations

import json
from typing import Any

from firstcall.corpus.notes import NOTE_SEEDS, SCRIPTS, read_jsonl
from firstcall.evaluation import note_score, voice_score
from firstcall.evaluation.note_score import names_in, person_names, unsupported
from firstcall.evaluation.voice_score import (
    UNSUPPORTED_MEASURE,
    edits,
    same_sound,
    spelling_groups,
    unsupported_by_sound,
    wer,
    words,
)

SCRIPT = "Called Stephen this morning. Budget is around $450k."


def _answer(**fields: Any) -> dict[str, Any]:
    return {"clean_note": "", "budget": None, "authority": None, "need": None,
            "timeline": None, "notes": None, "actions": [], **fields}  # fmt: skip


def test_words_are_lower_case_without_punctuation_and_numbers_are_values() -> None:
    assert words("Called Stephen, then Texted him!") == [
        "called", "stephen", "then", "texted", "him",
    ]  # fmt: skip
    for budget in ("$450k", "$450,000", "450 thousand", "four hundred fifty thousand dollars",
                   "450K", "450 grand"):  # fmt: skip
        assert words(f"Budget is {budget}.") == ["budget", "is", "450000"], budget
    assert words("$1.2 million") == words("one million two hundred thousand dollars")
    assert words("between 380 and 420 thousand") == ["between", "380000", "and", "420000"]
    assert words("Has 2 dogs.") == ["has", "2", "dogs"]


def test_edits_count_substitutions_deletions_and_insertions() -> None:
    assert edits(["a", "b", "c"], ["a", "b", "c"]) == 0
    assert edits(["a", "b", "c"], ["a", "x", "c"]) == 1
    assert edits(["a", "b", "c"], ["a", "c"]) == 1
    assert edits(["a", "b", "c"], ["a", "b", "c", "d"]) == 1
    assert edits(["a", "b", "c"], []) == 3
    assert edits([], ["a"]) == 1


def test_wer_is_all_edits_over_all_script_words() -> None:
    pairs = [
        ("Called Stephen this morning.", "Called Steven this morning"),
        ("Budget is around $450k.", "budget is around four hundred fifty thousand dollars"),
        ("Must have a pool.", "Must have a pool and a garage."),
    ]
    assert wer(pairs) == {"errors": 4, "words": 12, "wer": round(4 / 12, 4)}
    assert wer([("Budget is $450k.", "Budget is $405k.")])["errors"] == 1
    assert wer([]) == {"errors": 0, "words": 0, "wer": None}


def test_a_name_spelled_another_way_is_supported_for_voice_only() -> None:
    answer = _answer(clean_note="Called Steven this morning.")
    assert unsupported(SCRIPT, answer) == ["clean_note: name Steven"]
    assert unsupported_by_sound(SCRIPT, answer) == []


def test_numbers_and_other_names_stay_strict() -> None:
    answer = _answer(notes="Robert says $405k.", budget={"text": "", "min": 405000, "max": None})
    strict = unsupported(SCRIPT, answer)
    assert len(strict) == 3
    assert "notes: name Robert" in strict
    assert unsupported_by_sound(SCRIPT, answer) == strict


def test_the_score_counts_names_by_sound_and_shows_the_strict_count_beside_it() -> None:
    scripts = [{"id": "a", "text": SCRIPT}, {"id": "b", "text": "Emailed Brian the plans."}]
    labels = [
        {"id": s["id"], "buyer": None, "kind": "neither", "actions": [], "budget": None,
         "authority": "unknown", "need": None, "timeline": None,
         "terms": {"authority": [], "budget": [], "need": [], "timeline": []}, "seen": seen}
        for s, seen in zip(scripts, (True, False), strict=True)
    ]  # fmt: skip
    answers = {"a": _answer(notes="Steven called."), "b": _answer(notes="Bryan and Robert.")}
    report = voice_score.score(scripts, labels, answers)
    whole = report["all"]
    assert whole["unsupported"] == {"count": 1, "ids": ["b"]}
    assert whole["unsupported_strict"] == {"count": 2, "ids": ["a", "b"]}
    floor = [row for row in whole["floors"] if row["measure"].startswith(UNSUPPORTED_MEASURE)]
    assert floor == [{"measure": f"{UNSUPPORTED_MEASURE}, names by sound", "floor": "<= 1",
                      "value": 1, "met": True}]  # fmt: skip
    assert whole["strict"] == {"measure": f"{UNSUPPORTED_MEASURE}, names by spelling",
                               "floor": "reported", "value": 2, "met": None}  # fmt: skip
    assert len(whole["floors"]) == len(note_score.floors(whole))
    assert report["seen"]["unsupported"]["count"] == 0
    assert report["seen"]["unsupported_strict"]["count"] == 1
    assert report["unseen"]["unsupported"]["ids"] == ["b"]
    # The note scorer, given the same answers, still counts both.
    assert note_score.score(scripts, labels, answers)["all"]["unsupported"]["count"] == 2


def test_all_met_uses_the_floor_by_sound() -> None:
    texts = {"a": SCRIPT}
    labels = [{"id": "a", "buyer": None, "kind": "neither", "actions": [], "budget": None,
               "authority": "unknown", "need": None, "timeline": None,
               "terms": {"authority": [], "budget": [], "need": [], "timeline": []},
               "seen": True}]  # fmt: skip
    part = voice_score.score_part(texts, labels, {"a": _answer(notes="Steven")})
    assert part["unsupported"]["count"] == 0
    assert part["unsupported_strict"]["count"] == 1
    # The other floors have no cases here, so they are not met either way.
    assert voice_score.all_met(part) is False
    assert [r["met"] for r in voice_score.floors(part)][-1] is True


def test_the_spelling_table_holds_only_scorer_names_each_once() -> None:
    groups = spelling_groups()
    names = [name for group in groups for name in group]
    assert len(names) == len(set(names))
    assert all(len(group) >= 2 for group in groups)
    assert set(names) <= person_names()
    assert same_sound("Stephen") == {"Stephen", "Steven"}
    assert same_sound("Dana") == {"Dana"}


def test_every_spelling_group_holds_a_name_from_a_note_script() -> None:
    used: set[str] = set()
    for seed in NOTE_SEEDS:
        for script in read_jsonl(seed, SCRIPTS):
            used |= names_in(script["text"])
    assert all(group & used for group in spelling_groups())


def test_the_spelling_table_file_is_sorted_groups() -> None:
    groups = json.loads(voice_score.SPELLINGS.read_text())["groups"]
    assert groups == sorted(sorted(group) for group in groups)
