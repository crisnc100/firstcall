"""The Anthropic transport against a mocked HTTP layer. No test reaches the network."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
from firstcall.model.anthropic import API_URL, AnthropicTransport
from pydantic import SecretStr

from firstcall.model.transport import (
    NoProviderError,
    TransportError,
    TransportRequest,
    make_transport,
)
from firstcall.settings import Settings

KEY = "test-key-not-real"
SCHEMA = {"type": "object", "properties": {"category": {"type": "string"}}}
REQUEST = TransportRequest(
    model="claude-haiku-5-5",
    system="You read one message.",
    user="<message>\nName: Ana Ruiz\n</message>",
    schema=SCHEMA,
    max_output_tokens=400,
    timeout_seconds=8.0,
)


def reply(
    text: str = '{"category": "lead"}',
    stop: str = "end_turn",
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "usage": usage or {"input_tokens": 812, "output_tokens": 41},
    }


class Server:
    """A scripted HTTP answer per call, and every request it saw."""

    def __init__(self, *answers: httpx.Response | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def body(self, index: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[index].content)

    def transport(self) -> AnthropicTransport:
        return AnthropicTransport(KEY, httpx.Client(transport=httpx.MockTransport(self)))


def ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload)


def test_the_request_carries_the_schema_the_system_prompt_and_only_the_user_turn() -> None:
    server = Server(ok(reply()))
    answer = server.transport().complete(REQUEST)
    sent = server.requests[0]
    assert str(sent.url) == API_URL
    assert sent.headers["x-api-key"] == KEY
    assert sent.headers["anthropic-version"]
    body = server.body()
    assert body["model"] == "claude-haiku-5-5"
    assert body["max_tokens"] == 400
    assert body["system"] == REQUEST.system
    assert body["messages"] == [{"role": "user", "content": REQUEST.user}]
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    for sampling in ("temperature", "top_p", "top_k"):
        assert sampling not in body
    assert (answer.content, answer.prompt_tokens, answer.completion_tokens) == (
        '{"category": "lead"}',
        812,
        41,
    )
    assert answer.cost_usd is None


def test_the_timeout_is_passed_to_the_http_call() -> None:
    server = Server(ok(reply()))
    server.transport().complete(REQUEST)
    timeout = server.requests[0].extensions["timeout"]
    assert timeout["read"] == 8.0
    assert timeout["connect"] == 8.0


def test_cache_tokens_count_as_prompt_tokens() -> None:
    usage = {
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_creation_input_tokens": 20,
        "cache_read_input_tokens": 30,
    }
    answer = Server(ok(reply(usage=usage))).transport().complete(REQUEST)
    assert answer.prompt_tokens == 150


def test_thinking_text_is_left_out_of_the_answer() -> None:
    payload = reply()
    payload["content"].insert(0, {"type": "thinking", "thinking": "", "signature": "x"})
    assert Server(ok(payload)).transport().complete(REQUEST).content == '{"category": "lead"}'


@pytest.mark.parametrize("stop", ["max_tokens", "refusal", "model_context_window_exceeded"])
def test_a_cut_or_refused_answer_is_unreadable_and_still_charged(stop: str) -> None:
    answer = Server(ok(reply('{"category": "le', stop=stop))).transport().complete(REQUEST)
    assert answer.content == ""
    assert (answer.prompt_tokens, answer.completion_tokens) == (812, 41)


@pytest.mark.parametrize("status", [429, 500, 503, 529, 401, 404])
def test_http_errors_are_transport_errors(status: int) -> None:
    server = Server(httpx.Response(status, json={"type": "error", "error": {"message": "x"}}))
    with pytest.raises(TransportError):
        server.transport().complete(REQUEST)


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("slow"),
        httpx.ConnectTimeout("slow"),
        httpx.ConnectError("refused"),
        httpx.RemoteProtocolError("dropped"),
    ],
)
def test_timeouts_and_dropped_connections_are_transport_errors(failure: Exception) -> None:
    with pytest.raises(TransportError):
        Server(failure).transport().complete(REQUEST)


def test_an_answer_that_is_not_a_message_is_a_transport_error() -> None:
    with pytest.raises(TransportError):
        Server(httpx.Response(200, text="<html>gateway</html>")).transport().complete(REQUEST)


def test_a_refused_schema_falls_back_to_json_in_the_prompt_and_stays_there(
    caplog: pytest.LogCaptureFixture,
) -> None:
    refused = httpx.Response(
        400,
        json={
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "compiled grammar is too large"},
        },
    )
    server = Server(refused, ok(reply()), ok(reply()))
    transport = server.transport()
    with caplog.at_level(logging.INFO, logger="firstcall.model.anthropic"):
        first = transport.complete(REQUEST)
        transport.complete(REQUEST)
    assert first.content == '{"category": "lead"}'
    assert "format" in server.body(0)["output_config"]
    assert "format" not in server.body(1)["output_config"]
    assert "format" not in server.body(2)["output_config"]
    assert transport.output_mode == "json_prompt"
    assert "json_prompt" in caplog.text
    assert KEY not in caplog.text


def test_another_bad_request_is_a_transport_error_and_keeps_the_schema() -> None:
    bad = httpx.Response(
        400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "x"}}
    )
    server = Server(bad, ok(reply()))
    transport = server.transport()
    with pytest.raises(TransportError):
        transport.complete(REQUEST)
    transport.complete(REQUEST)
    assert "format" in server.body(1)["output_config"]
    assert transport.output_mode == "json_schema"


def test_errors_never_carry_the_key() -> None:
    server = Server(httpx.Response(401, json={"type": "error", "error": {"message": "bad key"}}))
    with pytest.raises(TransportError) as raised:
        server.transport().complete(REQUEST)
    assert KEY not in str(raised.value)


def test_make_transport_builds_the_anthropic_transport_for_a_claude_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("FIRSTCALL_MODEL_MODE", "FIRSTCALL_MODEL_NAME", "FIRSTCALL_MODEL_KEY"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(model_mode="live", model_name="claude-haiku-5-5", model_key=SecretStr(KEY))
    assert isinstance(make_transport(settings), AnthropicTransport)
    other = Settings(model_mode="live", model_name="openai/gpt-4o-mini", model_key=SecretStr(KEY))
    with pytest.raises(NoProviderError):
        make_transport(other)
