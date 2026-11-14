"""The note reader's answer: its schema, from the ADR-007 vocabulary, and a parser that never
raises.

A suggested action must quote the note's own words; one whose quote is not in the note is
dropped, so a model cannot suggest a contact the note never describes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from firstcall.core.note_terms import ACTION_KINDS, AUTHORITIES, EMPTY_AUTHORITY

MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
# A model that writes a quote also writes it a little differently: case, spacing and the
# final full stop do not matter.
QUOTE_TRIM = " .\"'"


@dataclass(frozen=True)
class Budget:
    text: str | None
    min: int | None
    max: int | None


@dataclass(frozen=True)
class Suggested:
    kind: str
    quote: str


@dataclass(frozen=True)
class NoteAnswer:
    clean_note: str | None = None
    budget: Budget | None = None
    authority: str = EMPTY_AUTHORITY
    authority_others: str | None = None
    need: str | None = None
    timeline_text: str | None = None
    timeline_month: str | None = None
    notes: str | None = None
    actions: tuple[Suggested, ...] = ()

    def as_json(self) -> dict[str, Any]:
        """The answer as the scorer and the stored note read it."""
        budget = self.budget
        timeline = None
        if self.timeline_text is not None or self.timeline_month is not None:
            timeline = {"text": self.timeline_text, "month": self.timeline_month}
        return {
            "clean_note": self.clean_note,
            "budget": None
            if budget is None
            else {"text": budget.text, "min": budget.min, "max": budget.max},
            "authority": {"value": self.authority, "others": self.authority_others},
            "need": self.need,
            "timeline": timeline,
            "notes": self.notes,
            "actions": [{"kind": a.kind, "quote": a.quote} for a in self.actions],
        }


def _nullable(kind: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [kind, {"type": "null"}]}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def answer_schema() -> dict[str, Any]:
    text = {"type": "string"}
    dollars = _nullable({"type": "integer", "minimum": 0})
    return _object(
        {
            "clean_note": _nullable(text),
            "budget": _nullable(_object({"text": _nullable(text), "min": dollars, "max": dollars})),
            "authority": _object(
                {"value": {"type": "string", "enum": list(AUTHORITIES)}, "others": _nullable(text)}
            ),
            "need": _nullable(text),
            "timeline": _nullable(
                _object(
                    {
                        "text": _nullable(text),
                        "month": _nullable({"type": "string", "pattern": MONTH.pattern}),
                    }
                )
            ),
            "notes": _nullable(text),
            "actions": {
                "type": "array",
                "items": _object(
                    {"kind": {"type": "string", "enum": list(ACTION_KINDS)}, "quote": text}
                ),
            },
        }
    )


class _UnreadableError(Exception):
    pass


def _json_object(content: str) -> dict[str, Any] | None:
    candidates = [content]
    start, end = content.find("{"), content.rfind("}")
    if 0 <= start < end:
        candidates.append(content[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, RecursionError):
            continue
        if isinstance(value, dict):
            return cast(dict[str, Any], value)
    return None


def _part(data: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _UnreadableError(key)
    return cast(dict[str, Any], value)


def _text(data: Mapping[str, Any] | None, key: str) -> str | None:
    if data is None:
        return None
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _UnreadableError(key)
    return value.strip() or None


def _dollars(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _UnreadableError(key)
    return value


def _squash(text: str) -> str:
    return " ".join(text.casefold().split()).strip(QUOTE_TRIM)


def _actions(data: Mapping[str, Any], note_text: str) -> tuple[Suggested, ...]:
    listed = data.get("actions")
    if listed is None:
        return ()
    if not isinstance(listed, list):
        raise _UnreadableError("actions")
    note = _squash(note_text)
    found: dict[str, Suggested] = {}
    for item in cast(list[Any], listed):
        if not isinstance(item, dict):
            raise _UnreadableError("actions")
        entry = cast(dict[str, Any], item)
        kind, quote = _text(entry, "kind"), _text(entry, "quote")
        if kind not in ACTION_KINDS:
            raise _UnreadableError("actions.kind")
        # The note's own words, or nothing: a quote the note does not hold is dropped.
        if quote is None or not _squash(quote) or _squash(quote) not in note:
            continue
        found.setdefault(kind, Suggested(kind, quote))
    return tuple(found.values())


def parse_note_answer(content: str, note_text: str) -> NoteAnswer | None:
    """The answer, or None when it is unreadable: not a JSON object, a value of the wrong
    type, or an authority or action kind outside ADR-007. A month that is not YYYY-MM is
    read as absent."""
    data = _json_object(content)
    if data is None:
        return None
    try:
        budget_part = _part(data, "budget")
        budget = None
        if budget_part is not None:
            budget = Budget(
                _text(budget_part, "text"),
                _dollars(budget_part, "min"),
                _dollars(budget_part, "max"),
            )
            if budget == Budget(None, None, None):
                budget = None
        authority_part = _part(data, "authority")
        authority = _text(authority_part, "value") or EMPTY_AUTHORITY
        if authority not in AUTHORITIES:
            raise _UnreadableError("authority")
        timeline_part = _part(data, "timeline")
        month = _text(timeline_part, "month")
        return NoteAnswer(
            clean_note=_text(data, "clean_note"),
            budget=budget,
            authority=authority,
            authority_others=_text(authority_part, "others"),
            need=_text(data, "need"),
            timeline_text=_text(timeline_part, "text"),
            timeline_month=month if month is not None and MONTH.fullmatch(month) else None,
            notes=_text(data, "notes"),
            actions=_actions(data, note_text),
        )
    except _UnreadableError:
        return None
