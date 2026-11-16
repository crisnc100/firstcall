"""Each recorded answer keeps the output mode it was made in, and a note eval counts JSON-in-
the-prompt answers from the recordings it used, so a resumed recording cannot pass fallback
answers off as structured output."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from firstcall.evaluation.note_eval import (
    RECORDINGS,
    IncompleteRecordingError,
    NoteRun,
    record_and_report,
)
from firstcall.model.recordings import Recordings
from firstcall.model.transport import TransportError, TransportRequest
from tests.model_fakes import MODEL, PRICES, FakeTransport
from tests.test_anthropic_transport import REQUEST, Server, ok, reply

PAID_MODEL = "claude-haiku-5-5"


def _answering(**fields: Any) -> FakeTransport:
    from tests.test_note_eval import _gold_answers

    answers = _gold_answers(7)

    def respond(request: TransportRequest) -> str:
        start = request.user.index("<note>\n") + len("<note>\n")
        return answers[request.user[start : request.user.rindex("\n</note>")]]

    return FakeTransport(respond=respond, **fields)


def _run(transport: FakeTransport, folder: Path) -> NoteRun:
    return NoteRun(mode="record", model=MODEL, budget_usd=Decimal("1.00"),
                   recordings=folder, transport=transport, prices=PRICES)  # fmt: skip


def test_a_resumed_recording_keeps_its_fallback_answers_flagged(
    database_url: str, tmp_path: Path
) -> None:
    # The first pass answers the first script in JSON in the prompt, then fails the second.
    first = _answering(output_mode="json_prompt")
    assert first.respond is not None
    first.script = [first.respond(_probe()), TransportError("x"), TransportError("x")]
    with pytest.raises(IncompleteRecordingError):
        record_and_report(7, database_url, _run(first, tmp_path), limit=2)
    # The resume finds the first answer already recorded and makes only the second call.
    second = _answering()
    report = record_and_report(7, database_url, _run(second, tmp_path), limit=2)
    assert len(second.requests) == 1
    assert report["calls"]["json_prompt_calls"] == 1
    assert report["structured_output"] is False


def _probe() -> TransportRequest:
    """The request the eval sends for seed 7's first script."""
    from firstcall.core.note_read import answer_schema
    from firstcall.corpus.notes import SCRIPTS, read_jsonl
    from firstcall.evaluation.note_eval import ZONE
    from firstcall.model.prompts import READ_NOTE, SYSTEM_PROMPTS, user_message
    from firstcall.notes.reader import context_text

    script = read_jsonl(7, SCRIPTS)[0]
    return TransportRequest(
        model=MODEL, system=SYSTEM_PROMPTS[READ_NOTE],
        user=user_message(context_text(script["note_date"], ZONE), script["text"], READ_NOTE),
        schema=answer_schema(), max_output_tokens=600, timeout_seconds=8.0,
    )  # fmt: skip


def test_an_answer_keeps_the_mode_it_was_made_in(tmp_path: Path) -> None:
    transport = Server(
        httpx.Response(400, json={"type": "error", "error": {"message": "schema refused"}}),
        ok(reply()),
    ).transport()
    assert transport.complete(REQUEST).output_mode == "json_prompt"
    assert Server(ok(reply())).transport().complete(REQUEST).output_mode == "json_schema"


def test_every_committed_note_answer_says_it_was_structured() -> None:
    lines = (RECORDINGS / f"notes-{PAID_MODEL}.jsonl").read_text().splitlines()
    assert lines
    assert {json.loads(line)["output_mode"] for line in lines} == {"json_schema"}
    recordings = Recordings(RECORDINGS).for_task("read_note")
    assert all(r.output_mode == "json_schema" for r in recordings._for(PAID_MODEL).values())  # pyright: ignore[reportPrivateUsage]
