"""The model answer's parser and the per-field choice between rules and model."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from firstcall.core.model_read import (
    DEFAULT_METHODS,
    FIELDS,
    ModelAnswer,
    answer_schema,
    combine,
    parse_answer,
)
from firstcall.core.read import AgentRef, Reading
from tests.model_fakes import answer

ITEMS = ["Juniper Mesa", "Copperleaf"]
TEAMS = ["east", "west"]


def parse(content: str) -> ModelAnswer | None:
    return parse_answer(content, ITEMS, TEAMS)


def test_a_full_answer_is_read_and_normalised() -> None:
    content = answer(
        name=" Ana Ruiz ",
        phone="(202) 555-0142",
        email="Ana.Ruiz@Inbox.example",
        item="Juniper Mesa",
        agent={"name": "Dana Whitcomb", "email": "DANA@keyhaven.example", "phone": None},
        category="lead",
        team="west",
    )
    assert parse(content) == ModelAnswer(
        category="lead",
        name="Ana Ruiz",
        phone="+12025550142",
        email="ana.ruiz@inbox.example",
        item="Juniper Mesa",
        agent=AgentRef("Dana Whitcomb", "dana@keyhaven.example", None),
        team="west",
    )


def test_an_object_wrapped_in_a_code_fence_is_read() -> None:
    parsed = parse("Here you go:\n```json\n" + answer(category="vendor") + "\n```")
    assert parsed is not None
    assert parsed.category == "vendor"


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        "[1, 2]",
        "{broken",
        answer(category="partner"),
        answer(category=None),
        answer(team="north"),
        answer(item="A House We Do Not Sell"),
        answer(name=42),
        answer(agent="Dana"),
        json.dumps({"name": "Ana"}),
    ],
)
def test_an_unreadable_answer_is_none(content: str) -> None:
    assert parse(content) is None


def test_contacts_that_do_not_normalise_are_absent() -> None:
    parsed = parse(answer(phone="call me", email="nope", name="  ", agent={"name": ""}))
    assert parsed == ModelAnswer(category="lead")


def test_the_schema_closes_category_item_and_team() -> None:
    schema = answer_schema(ITEMS, TEAMS)
    properties = schema["properties"]
    assert properties["category"]["enum"] == ["lead", "vendor", "support", "spam"]
    assert properties["item"]["anyOf"][0]["enum"] == ITEMS
    assert properties["team"]["anyOf"][0]["enum"] == TEAMS
    assert set(schema["required"]) == set(properties)
    assert schema["additionalProperties"] is False


RULES = Reading(
    category="lead",
    name=None,
    phone="+12025550142",
    email="ana@inbox.example",
    item=None,
    team_hint=None,
    unsure=("email", "name"),
)
MODEL = ModelAnswer(
    category="vendor",
    name="Ana Ruiz",
    phone="+12025550199",
    email="ana.ruiz@inbox.example",
    item="Juniper Mesa",
    agent=AgentRef("Dana Whitcomb", None, None),
    team="west",
)


def test_combined_keeps_sure_rules_values_and_fills_the_rest_from_the_model() -> None:
    reading = combine(RULES, MODEL, DEFAULT_METHODS)
    assert reading.phone == "+12025550142"
    assert reading.email == "ana.ruiz@inbox.example"
    assert reading.name == "Ana Ruiz"
    assert reading.item == "Juniper Mesa"
    assert reading.agent == AgentRef("Dana Whitcomb", None, None)
    assert reading.category == "vendor"
    assert reading.team_hint == "west"
    assert reading.unsure == ()


def test_combined_keeps_an_unsure_rules_value_when_the_model_has_none() -> None:
    reading = combine(RULES, ModelAnswer(category="lead"), DEFAULT_METHODS)
    assert reading.email == "ana@inbox.example"
    assert reading.unsure == ("email", "name")


def test_a_rules_spam_signal_holds_under_combined_but_not_under_model() -> None:
    spam = replace(RULES, category="spam", spam_signals=("honeypot",))
    lead = ModelAnswer(category="lead")
    assert combine(spam, lead, DEFAULT_METHODS).category == "spam"
    assert combine(spam, lead, {**DEFAULT_METHODS, "category": "model"}).category == "lead"


def test_rules_and_model_methods_take_one_side_only() -> None:
    rules = combine(RULES, MODEL, dict.fromkeys(FIELDS, "rules"))
    assert (rules.name, rules.email, rules.category, rules.team_hint) == (
        None,
        "ana@inbox.example",
        "lead",
        None,
    )
    assert rules.agent is None
    model = combine(RULES, MODEL, dict.fromkeys(FIELDS, "model"))
    assert (model.phone, model.category, model.team_hint) == ("+12025550199", "vendor", "west")
