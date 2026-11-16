"""The note answer schema stays within what the API's structured output accepts, and a run
whose transport fell back to JSON in the prompt says so in its report."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from firstcall.core.note_read import answer_schema, parse_note_answer
from firstcall.evaluation.note_eval import NoteRun, _markdown, record_and_report
from tests.model_fakes import MODEL, PRICES, FakeTransport
from tests.test_anthropic_transport import REQUEST, Server, ok, reply
from tests.test_note_read import NOTE
from tests.test_note_reader import note_answer


def _keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        found = set(node)
        for value in node.values():
            found |= _keys(value)
        return found
    if isinstance(node, list):
        return set().union(*(_keys(item) for item in node)) if node else set()
    return set()


def test_the_answer_schema_has_no_keyword_structured_output_refuses() -> None:
    keys = _keys(answer_schema())
    assert "minimum" not in keys


def test_a_negative_budget_bound_is_dropped_and_the_rest_kept() -> None:
    answer = parse_note_answer(
        note_answer(budget={"text": "around $450k", "min": -5, "max": 450000}), NOTE
    )
    assert answer is not None
    assert answer.budget is not None
    assert (answer.budget.min, answer.budget.max) == (None, 450000)
    assert answer.need is not None


def test_a_month_that_is_not_a_month_is_dropped() -> None:
    answer = parse_note_answer(note_answer(timeline={"text": "soon", "month": "June"}), NOTE)
    assert answer is not None
    assert (answer.timeline_text, answer.timeline_month) == ("soon", None)


def test_the_transport_counts_calls_sent_without_the_schema() -> None:
    refused = httpx.Response(
        400,
        json={
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "schema is not supported"},
        },
    )
    transport = Server(refused, ok(reply()), ok(reply())).transport()
    assert transport.json_prompt_calls == 0
    transport.complete(REQUEST)
    transport.complete(REQUEST)
    assert transport.json_prompt_calls == 2


class _FallenBack(FakeTransport):
    json_prompt_calls = 3


def test_a_run_that_fell_back_is_marked_not_on_structured_output(
    database_url: str, tmp_path: Path
) -> None:
    from tests.test_note_eval import _gold_answers

    answers = _gold_answers(7)

    def respond(request: Any) -> str:
        start = request.user.index("<note>\n") + len("<note>\n")
        return answers[request.user[start : request.user.rindex("\n</note>")]]

    def run_with(transport: FakeTransport, folder: Path) -> dict[str, Any]:
        run_args = NoteRun(mode="record", model=MODEL, budget_usd=Decimal("1.00"),
                           recordings=folder, transport=transport, prices=PRICES)  # fmt: skip
        return record_and_report(7, database_url, run_args, limit=2)

    fell_back = run_with(_FallenBack(respond=respond), tmp_path / "a")
    assert fell_back["record_pass"]["json_prompt_calls"] == 3
    assert fell_back["structured_output"] is False
    assert "NOT on structured output: 3 calls" in _markdown(fell_back)
    clean = run_with(FakeTransport(respond=respond), tmp_path / "b")
    assert clean["record_pass"]["json_prompt_calls"] == 0
    assert clean["structured_output"] is True
    assert "NOT on structured output" not in _markdown(clean)
    json.dumps(clean)
