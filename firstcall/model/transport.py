"""The one interface between the gateway and a model provider.

A transport sends one request and returns the answer with its token counts. It knows nothing
of budgets, modes or logging; the gateway owns those. No provider transport is wired yet: the
provider is not decided, so live and record modes refuse to start (see `make_transport`).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from firstcall.settings import Settings


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
    """Live or record mode was asked for, but this build has no provider transport."""


def make_transport(settings: Settings) -> Transport:
    raise NoProviderError(
        f"FIRSTCALL_MODEL_MODE={settings.model_mode} needs a provider transport, and none is "
        "wired in this build. Use offline or replay."
    )
