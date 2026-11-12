"""The one interface between the gateway and a model provider.

A transport sends one request and returns the answer with its token counts. It knows nothing
of budgets or modes; the gateway owns those. The one provider wired is the Anthropic API
(`firstcall/model/anthropic.py`); any other model name refuses to start in live or record
mode (see `make_transport`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from firstcall.settings import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransportRequest:
    model: str
    system: str
    user: str
    # The JSON schema the answer must follow, for providers that enforce structured output.
    schema: dict[str, Any]
    max_output_tokens: int
    timeout_seconds: float


@dataclass(frozen=True)
class TransportReply:
    content: str
    prompt_tokens: int
    completion_tokens: int
    # The provider's own figure when it reports one; otherwise the gateway prices the tokens.
    cost_usd: Decimal | None = None


class TransportError(Exception):
    """The call failed or timed out. The gateway retries once."""


class Transport(Protocol):
    def complete(self, request: TransportRequest) -> TransportReply: ...


class NoProviderError(Exception):
    """Live or record mode was asked for a model with no provider transport."""


def make_transport(settings: Settings) -> Transport:
    """The provider for the configured model. Claude models go to the Anthropic API."""
    if settings.model_name.startswith("claude-"):
        from firstcall.model.anthropic import AnthropicTransport

        log.info("model transport: Anthropic API for %s", settings.model_name)
        return AnthropicTransport(settings.model_key.get_secret_value())
    raise NoProviderError(
        f"no provider transport for {settings.model_name!r}; only Claude models on the "
        "Anthropic API are wired. Use offline or replay."
    )
