"""A test transport and gateway builders. No test talks to a model provider."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from firstcall.core.clock import Clock
from firstcall.model.gateway import Gateway, GatewayConfig
from firstcall.model.prices import Price
from firstcall.model.recordings import Recordings
from firstcall.model.synthetic import SyntheticIndex
from firstcall.model.transport import TransportError, TransportReply, TransportRequest

MODEL = "test/model"
PRICES = {MODEL: Price(Decimal("1.00"), Decimal("2.00"))}


def answer(**fields: Any) -> str:
    body: dict[str, Any] = {
        "name": None,
        "phone": None,
        "email": None,
        "item": None,
        "agent": None,
        "category": "lead",
        "team": None,
    }
    body.update(fields)
    return json.dumps(body)


@dataclass
class FakeTransport:
    """Answers from a script, in order. A TransportError in the script is raised instead."""

    script: list[str | TransportError] = field(default_factory=list[str | TransportError])
    default: str | None = None
    prompt_tokens: int = 1000
    completion_tokens: int = 100
    cost_usd: Decimal | None = None
    delay_seconds: float = 0.0
    requests: list[TransportRequest] = field(default_factory=list[TransportRequest])
    respond: Callable[[TransportRequest], str] | None = None

    def complete(self, request: TransportRequest) -> TransportReply:
        self.requests.append(request)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.script:
            step = self.script.pop(0)
        elif self.respond is not None:
            step = self.respond(request)
        else:
            assert self.default is not None, "the fake transport ran out of answers"
            step = self.default
        if isinstance(step, TransportError):
            raise step
        return TransportReply(step, self.prompt_tokens, self.completion_tokens, self.cost_usd)


def config(mode: str = "live", **overrides: Any) -> GatewayConfig:
    values: dict[str, Any] = {
        "mode": mode,
        "model": MODEL,
        "has_key": True,
        "off": False,
        "daily_cap_usd": Decimal("1.00"),
        "timeout_seconds": 8.0,
        "max_output_tokens": 400,
    }
    values.update(overrides)
    return GatewayConfig(**values)


def gateway(
    sessions: sessionmaker[Session],
    clock: Clock,
    transport: FakeTransport | None = None,
    mode: str = "live",
    recordings: Path | None = None,
    synthetic: SyntheticIndex | None = None,
    **overrides: Any,
) -> Gateway:
    return Gateway(
        sessions,
        clock,
        config(mode, **overrides),
        transport=transport if transport is not None else FakeTransport(default=answer()),
        recordings=Recordings(recordings) if recordings is not None else None,
        synthetic=synthetic,
        prices=PRICES,
    )
