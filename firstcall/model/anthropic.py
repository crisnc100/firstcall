"""The Anthropic Messages API, called over HTTPS with httpx (already a dependency).

What each request sends, and why:
- The fixed system prompt as `system`, and the intake text only in the one user turn.
- `output_config.format` with Firstcall's JSON schema. If the API refuses the schema (a 400
  that names the schema or its grammar), the transport switches, for the rest of the
  process, to plain JSON asked for in the prompt; the gateway's parser validates either way.
  The mode in use is logged and kept in `output_mode`.
- Thinking off and effort low: reading one message into a short fixed form needs neither,
  and thinking tokens are billed as output.
- No temperature, top_p or top_k: the API refuses non-default values for this model.

An answer cut at max_tokens, or refused by the API's safety classifiers, comes back with its
token counts and empty text, so the gateway charges it and reads it as unreadable.
Rate limits (429), overload (529), server errors, timeouts and dropped connections are
TransportError; the gateway retries once. The key is sent as a header and never logged.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from firstcall.model.transport import TransportError, TransportReply, TransportRequest

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2026-10-21"
JSON_SCHEMA = "json_schema"
JSON_PROMPT = "json_prompt"
# Stop reasons whose text is not a whole answer.
CUT = frozenset({"max_tokens", "refusal", "model_context_window_exceeded"})
# Words in a 400's message that mean the API refused the output schema itself.
SCHEMA_REFUSED = ("grammar", "schema", "output_config", "format")

log = logging.getLogger(__name__)


class AnthropicTransport:
    def __init__(self, key: str, client: httpx.Client | None = None) -> None:
        self._key = key
        self._client = client or httpx.Client()
        self.output_mode = JSON_SCHEMA

    def _body(self, request: TransportRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "thinking": {"type": "disabled"},
        }
        config: dict[str, Any] = {"effort": "low"}
        if self.output_mode == JSON_SCHEMA:
            config["format"] = {"type": "json_schema", "schema": request.schema}
        body["output_config"] = config
        return body

    def _post(self, request: TransportRequest) -> httpx.Response:
        try:
            return self._client.post(
                API_URL,
                json=self._body(request),
                headers={
                    "x-api-key": self._key,
                    "anthropic-version": API_VERSION,
                    "content-type": "application/json",
                },
                timeout=httpx.Timeout(request.timeout_seconds),
            )
        except httpx.HTTPError as error:
            raise TransportError(f"{type(error).__name__}: no answer") from error

    def complete(self, request: TransportRequest) -> TransportReply:
        response = self._post(request)
        if response.status_code == 400 and self.output_mode == JSON_SCHEMA:
            message = _error_message(response)
            if any(word in message.lower() for word in SCHEMA_REFUSED):
                self.output_mode = JSON_PROMPT
                log.warning(
                    "structured output refused (%s); output mode now %s", message, JSON_PROMPT
                )
                response = self._post(request)
        if response.status_code != 200:
            raise TransportError(f"HTTP {response.status_code}: {_error_message(response)}")
        try:
            payload: dict[str, Any] = response.json()
            usage: dict[str, Any] = payload["usage"]
            prompt_tokens = (
                int(usage["input_tokens"])
                + int(usage.get("cache_creation_input_tokens") or 0)
                + int(usage.get("cache_read_input_tokens") or 0)
            )
            completion_tokens = int(usage["output_tokens"])
            blocks: list[dict[str, Any]] = payload["content"]
        except (ValueError, KeyError, TypeError) as error:
            raise TransportError("the answer is not a Messages API message") from error
        text = "".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")
        if payload.get("stop_reason") in CUT:
            log.info("answer stopped at %s; read as unreadable", payload.get("stop_reason"))
            text = ""
        return TransportReply(text, prompt_tokens, completion_tokens)


def _error_message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
        return str(error.get("message") or error.get("type") or "")[:300]
    except ValueError:
        return response.text[:300]
