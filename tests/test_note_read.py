"""The note reader's answer, without a database: its schema follows ADR-007, the parser never
raises, and a suggested action must quote the note."""

from __future__ import annotations

import json
from typing import Any

import pytest

from firstcall.core.note_read import Budget, NoteAnswer, Suggested, answer_schema, parse_note_answer
from firstcall.core.note_terms import ACTION_KINDS, AUTHORITIES
from firstcall.evaluation.note_score import unsupported

NOTE = "Called Ana this morning. Budget is around $450k. Wants to move by June."


def reply(**fields: Any) -> str:
    base: dict[str, Any] = {
        "clean_note": None, "budget": None, "authority": {"value": "unknown", "others": None},
        "need": None, "timeline": None, "notes": None, "actions": [],
    }  # fmt: skip
    return json.dumps({**base, **fields})


def test_the_schema_takes_its_values_from_adr_007() -> None:
    schema = answer_schema()
    properties = schema["properties"]
    assert set(schema["required"]) == {
        "clean_note", "budget", "authority", "need", "timeline", "notes", "actions",
    }  # fmt: skip
    assert properties["authority"]["properties"]["value"]["enum"] == list(AUTHORITIES)
    action = properties["actions"]["items"]["properties"]
    assert action["kind"]["enum"] == list(ACTION_KINDS)
    assert schema["additionalProperties"] is False


def test_a_full_answer_is_read() -> None:
    answer = parse_note_answer(
        reply(
            clean_note="Called Ana. Budget around $450k; moving by June.",
            budget={"text": "around $450k", "min": 450000, "max": 450000},
            authority={"value": "decision_maker", "others": None},
            need="three bedrooms",
            timeline={"text": "by June", "month": "2026-06"},
            notes="Prefers mornings.",
            actions=[{"kind": "call", "quote": "Called Ana this morning"}],
        ),
        NOTE,
    )
    assert answer == NoteAnswer(
        clean_note="Called Ana. Budget around $450k; moving by June.",
        budget=Budget("around $450k", 450000, 450000),
        authority="decision_maker",
        need="three bedrooms",
        timeline_text="by June",
        timeline_month="2026-06",
        notes="Prefers mornings.",
        actions=(Suggested("call", "Called Ana this morning"),),
    )


def test_a_suggestion_whose_quote_is_not_in_the_note_is_dropped() -> None:
    answer = parse_note_answer(
        reply(actions=[
            {"kind": "call", "quote": "called ana THIS morning."},
            {"kind": "email", "quote": "Emailed Ana the plans"},
            {"kind": "text", "quote": "  "},
            {"kind": "call", "quote": "Called Ana"},
        ]),
        NOTE,
    )  # fmt: skip
    assert answer is not None
    assert answer.actions == (Suggested("call", "called ana THIS morning."),)


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[1, 2]",
        reply(authority={"value": "boss", "others": None}),
        reply(actions=[{"kind": "fax", "quote": "Called Ana"}]),
        reply(actions="call"),
        reply(actions=["call"]),
        reply(budget={"text": "x", "min": -5, "max": None}),
        reply(budget={"text": "x", "min": 1.5, "max": None}),
        reply(budget={"text": "x", "min": True, "max": None}),
        reply(budget="450k"),
        reply(need=["garage"]),
    ],
)
def test_an_unreadable_answer_is_none(content: str) -> None:
    assert parse_note_answer(content, NOTE) is None


def test_empty_parts_read_as_absent_and_a_bad_month_is_dropped() -> None:
    answer = parse_note_answer(
        "Here you go: " + reply(
            budget={"text": " ", "min": None, "max": None},
            authority=None,
            timeline={"text": "June", "month": "June 2026"},
            need="   ",
        ),
        NOTE,
    )  # fmt: skip
    assert answer == NoteAnswer(timeline_text="June")


def test_the_answer_json_is_what_the_scorer_reads() -> None:
    answer = NoteAnswer(
        budget=Budget("around $450k", 450000, 450000),
        timeline_month="2026-06",
        actions=(Suggested("call", "Called Ana"),),
    )
    data = answer.as_json()
    assert data["budget"] == {"text": "around $450k", "min": 450000, "max": 450000}
    assert data["timeline"] == {"text": None, "month": "2026-06"}
    assert data["authority"] == {"value": "unknown", "others": None}
    assert data["actions"] == [{"kind": "call", "quote": "Called Ana"}]
    assert NoteAnswer().as_json()["timeline"] is None
    assert unsupported(NOTE, data) == []
