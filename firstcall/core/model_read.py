"""The model read's answer: its schema, a parser that never raises, and the per-field choice
between the rules read and the model.

Category, item and team are closed lists in the schema, and the parser refuses a value
outside them, so a model cannot invent a team or a category.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, cast

from firstcall.core.identity import normalize_email, normalize_phone
from firstcall.core.read import AgentRef, Reading

CATEGORIES = ("lead", "vendor", "support", "spam")
FIELDS = ("phone", "email", "name", "item", "agent", "category", "team")
METHODS = ("rules", "model", "combined")
CONTACT_FIELDS = ("phone", "email", "name", "item")
# Until the eval picks a method per field, every field takes the rules first and the model
# only where the rules found nothing or were unsure.
DEFAULT_METHODS: Mapping[str, str] = dict.fromkeys(FIELDS, "combined")


@dataclass(frozen=True)
class ModelAnswer:
    category: str
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    item: str | None = None
    agent: AgentRef | None = None
    team: str | None = None

    def as_json(self) -> dict[str, Any]:
        agent = self.agent
        return {
            "category": self.category,
            "name": self.name,
            "phone": self.phone,
            "email": self.email,
            "item": self.item,
            "agent": None
            if agent is None
            else {"name": agent.name, "email": agent.email, "phone": agent.phone},
            "team": self.team,
        }


def _nullable(kind: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [kind, {"type": "null"}]}


def answer_schema(items: Sequence[str], teams: Sequence[str]) -> dict[str, Any]:
    text = {"type": "string"}
    agent = {
        "type": "object",
        "properties": {"name": _nullable(text), "email": _nullable(text), "phone": _nullable(text)},
        "required": ["name", "email", "phone"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "name": _nullable(text),
            "phone": _nullable(text),
            "email": _nullable(text),
            "item": _nullable({"type": "string", "enum": list(items)}),
            "agent": _nullable(agent),
            "category": {"type": "string", "enum": list(CATEGORIES)},
            "team": _nullable({"type": "string", "enum": list(teams)}),
        },
        "required": ["name", "phone", "email", "item", "agent", "category", "team"],
        "additionalProperties": False,
    }


def _object(content: str) -> dict[str, Any] | None:
    """The JSON object in an answer. Falls back to the outermost braces, for a model that
    wraps its object in a code fence or a sentence."""
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


class _UnreadableError(Exception):
    pass


def _text(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _UnreadableError(key)
    return value.strip() or None


def _choice(data: Mapping[str, Any], key: str, allowed: Sequence[str]) -> str | None:
    value = _text(data, key)
    if value is not None and value not in allowed:
        raise _UnreadableError(key)
    return value


def _agent(data: Mapping[str, Any]) -> AgentRef | None:
    value = data.get("agent")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _UnreadableError("agent")
    fields = cast(dict[str, Any], value)
    agent = AgentRef(
        _text(fields, "name"),
        normalize_email(_text(fields, "email")),
        normalize_phone(_text(fields, "phone")),
    )
    return agent if (agent.name or agent.email or agent.phone) else None


def parse_answer(content: str, items: Sequence[str], teams: Sequence[str]) -> ModelAnswer | None:
    """The answer, or None when it is unreadable: not a JSON object, a value of the wrong
    type, or a category, item or team outside its list. A phone or email that does not
    normalise is read as absent, as the rules read treats it."""
    data = _object(content)
    if data is None:
        return None
    try:
        category = _choice(data, "category", CATEGORIES)
        if category is None:
            return None
        return ModelAnswer(
            category=category,
            name=_text(data, "name"),
            phone=normalize_phone(_text(data, "phone")),
            email=normalize_email(_text(data, "email")),
            item=_choice(data, "item", items),
            agent=_agent(data),
            team=_choice(data, "team", teams),
        )
    except _UnreadableError:
        return None


def _pick(method: str, rules: Any, model: Any, unsure: bool) -> Any:
    if method == "rules":
        return rules
    if method == "model":
        return model
    if rules is not None and not unsure:
        return rules
    return model if model is not None else rules


def combine(rules: Reading, answer: ModelAnswer, methods: Mapping[str, str]) -> Reading:
    """The reading the pipeline uses, field by field. Combined means the rules value unless
    the rules found nothing or marked the field unsure. A rules spam signal (a honeypot or a
    listed sender) is a fact about the message, so it holds under the combined method."""
    chosen: dict[str, Any] = {}
    for name in CONTACT_FIELDS:
        chosen[name] = _pick(
            methods[name], getattr(rules, name), getattr(answer, name), name in rules.unsure
        )
    chosen["agent"] = _pick(methods["agent"], rules.agent, answer.agent, False)
    category_method = methods["category"]
    if category_method == "rules":
        category = rules.category
    elif category_method == "model" or not rules.spam_signals:
        category = answer.category
    else:
        category = rules.category
    team_hint = _pick(methods["team"], rules.team_hint, answer.team, False)
    unsure = tuple(n for n in rules.unsure if chosen.get(n) is getattr(rules, n))
    return replace(
        rules,
        category=category,
        team_hint=team_hint,
        unsure=unsure,
        **chosen,
    )
