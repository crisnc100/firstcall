"""The model gateway: the only code that calls a model.

Every call goes through four checks in order: the kill switch, the mode, the key, and the
budget pre-check. Then it is made (or replayed) and logged with its model, tokens, cost and
latency. Intake text is placed only in the user message; the system prompt is a fixed
constant per task, so text written to steer a reader never gains a system prompt's weight.

Modes:
- replay: answers recorded from earlier runs. A miss raises ReplayMissError.
- record: real calls whose answers are saved; a request already recorded is answered from
  its recording. Input that is not from the synthetic generator raises NotSyntheticError
  before any call.
- live: real calls under the budget guard.
- offline: no calls; every message takes the rules path.

The budget is reserved before a call and settled after it, in the budget ledger, inside a
row lock. A crash during a call leaves the reservation charged, so the ledger can run high
but never low. A failed attempt keeps its reservation for the same reason: a provider may
bill a call that timed out on our side.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from firstcall.core.clock import Clock
from firstcall.db import BudgetLedger, ModelCall
from firstcall.model.prices import Price, load_prices
from firstcall.model.prompts import SYSTEM_PROMPTS, user_message
from firstcall.model.recordings import Recording, Recordings, request_key
from firstcall.model.synthetic import SyntheticIndex
from firstcall.model.transport import (
    Transport,
    TransportError,
    TransportReply,
    TransportRequest,
    make_transport,
)
from firstcall.settings import Settings

ATTEMPTS = 2
# Prompt tokens are estimated at one per three characters, more than English text uses, so
# the pre-check errs toward refusing.
CHARS_PER_TOKEN = 3

KILL_SWITCH = "kill_switch"
OFFLINE = "offline"
NO_KEY = "no_key"
BUDGET = "budget"
DOWN = "down"


class ModelUnavailableError(Exception):
    """No answer this time. The caller takes the rules path and marks the message for a
    reread. `reason` is one of the constants above."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ReplayMissError(Exception):
    """Replay mode has no recorded answer for this request."""


class NotSyntheticError(Exception):
    """Record mode was given input that is not from the synthetic generator."""


@dataclass(frozen=True)
class ModelRequest:
    org_id: uuid.UUID
    message_id: uuid.UUID | None
    task: str
    # The message as the model reads it. It goes only into the user message.
    intake_text: str
    # Org configuration the model chooses from (items, teams). Never message text.
    context: str
    schema: dict[str, Any]
    # The stored intake bytes, which record mode checks against the synthetic corpus.
    raw: bytes


@dataclass(frozen=True)
class ModelReply:
    model: str
    content: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    latency_ms: int


@dataclass(frozen=True)
class GatewayConfig:
    mode: str
    model: str
    has_key: bool
    off: bool
    daily_cap_usd: Decimal
    timeout_seconds: float
    max_output_tokens: int

    @classmethod
    def from_settings(cls, settings: Settings) -> GatewayConfig:
        return cls(
            mode=settings.model_mode,
            model=settings.model_name,
            has_key=bool(settings.model_key.get_secret_value()),
            off=settings.model_off,
            daily_cap_usd=settings.model_daily_cap_usd,
            timeout_seconds=settings.model_timeout_seconds,
            max_output_tokens=settings.model_max_output_tokens,
        )


class Gateway:
    def __init__(
        self,
        sessions: sessionmaker[Any],
        clock: Clock,
        config: GatewayConfig,
        *,
        transport: Transport | None = None,
        recordings: Recordings | None = None,
        synthetic: SyntheticIndex | None = None,
        prices: dict[str, Price] | None = None,
    ) -> None:
        self.sessions = sessions
        self.clock = clock
        self.config = config
        self.transport = transport
        self.recordings = recordings
        self.synthetic = synthetic
        self.price: Price | None = None
        if config.mode in ("live", "record", "replay") and not config.model:
            raise ValueError(f"model mode {config.mode} needs FIRSTCALL_MODEL_NAME")
        if config.mode in ("replay", "record") and recordings is None:
            raise ValueError(f"model mode {config.mode} needs a recordings folder")
        if config.mode == "record" and synthetic is None:
            raise ValueError("record mode needs the synthetic corpus index")
        if config.mode in ("live", "record"):
            if transport is None:
                raise ValueError(f"model mode {config.mode} needs a transport")
            listed = load_prices() if prices is None else prices
            if config.model not in listed:
                raise ValueError(f"no listed price for {config.model}; the budget cannot be kept")
            self.price = listed[config.model]

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        sessions: sessionmaker[Any],
        clock: Clock,
        transport: Transport | None = None,
    ) -> Gateway:
        config = GatewayConfig.from_settings(settings)
        live = config.mode in ("live", "record") and config.has_key and not config.off
        if live and transport is None:
            transport = make_transport(settings)
        if config.mode in ("live", "record") and transport is None:
            # Off or without a key, no call will be made, so no transport is needed; a
            # placeholder keeps the constructor's checks uniform.
            transport = _Unused()
        recordings = (
            Recordings(Path(settings.model_recordings))
            if config.mode in ("replay", "record")
            else None
        )
        synthetic = SyntheticIndex.from_gold() if config.mode == "record" else None
        return cls(
            sessions,
            clock,
            config,
            transport=transport,
            recordings=recordings,
            synthetic=synthetic,
        )

    def unavailable(self, org_id: uuid.UUID) -> str | None:
        """Why no call would be made now, or None when one would be tried."""
        config = self.config
        if config.off:
            return KILL_SWITCH
        if config.mode == "offline":
            return OFFLINE
        if config.mode in ("live", "record"):
            if not config.has_key:
                return NO_KEY
            if self._spent(org_id) >= config.daily_cap_usd:
                return BUDGET
        return None

    def call(self, request: ModelRequest) -> ModelReply:
        reason = self.unavailable(request.org_id)
        if reason is not None:
            raise ModelUnavailableError(reason)
        outbound = self.outbound(request)
        if self.config.mode == "replay":
            return self._replay(request, outbound)
        if self.config.mode == "record":
            assert self.synthetic is not None
            assert self.recordings is not None
            if request.raw not in self.synthetic:
                raise NotSyntheticError("record mode only sends messages from the synthetic corpus")
            if self.recordings.find(outbound) is not None:
                # Already recorded: answered from the recording, so a rerun spends nothing.
                return self._replay(request, outbound)
        return self._live(request, outbound)

    def outbound(self, request: ModelRequest) -> TransportRequest:
        """What the transport is sent: the task's fixed system prompt, and the intake text
        inside the user message."""
        return TransportRequest(
            model=self.config.model,
            system=SYSTEM_PROMPTS[request.task],
            user=user_message(request.context, request.intake_text),
            schema=request.schema,
            max_output_tokens=self.config.max_output_tokens,
            timeout_seconds=self.config.timeout_seconds,
        )

    def _replay(self, request: ModelRequest, outbound: TransportRequest) -> ModelReply:
        assert self.recordings is not None
        found = self.recordings.find(outbound)
        if found is None:
            raise ReplayMissError(
                f"no recorded answer for {outbound.model} (key {request_key(outbound)[:12]})"
            )
        reply = ModelReply(
            model=found.model,
            content=found.content,
            prompt_tokens=found.prompt_tokens,
            completion_tokens=found.completion_tokens,
            cost_usd=found.cost,
            latency_ms=found.latency_ms,
        )
        self._log(request, reply, ok=True)
        return reply

    def estimate(self, outbound: TransportRequest) -> Decimal:
        """The most a call should cost: its estimated prompt and a full-length answer."""
        assert self.price is not None
        chars = len(outbound.system) + len(outbound.user) + len(str(outbound.schema))
        prompt_tokens = -(-chars // CHARS_PER_TOKEN)
        return self.price.cost(prompt_tokens, outbound.max_output_tokens)

    def _live(self, request: ModelRequest, outbound: TransportRequest) -> ModelReply:
        assert self.transport is not None
        assert self.price is not None
        estimate = self.estimate(outbound)
        for _ in range(ATTEMPTS):
            self._reserve(request.org_id, estimate)
            started = time.monotonic()
            try:
                answer: TransportReply = self.transport.complete(outbound)
            except TransportError:
                failed = ModelReply(outbound.model, "", 0, 0, Decimal(0), _elapsed_ms(started))
                self._log(request, failed, ok=False)
                continue
            cost = answer.cost_usd
            if cost is None:
                cost = self.price.cost(answer.prompt_tokens, answer.completion_tokens)
            reply = ModelReply(
                model=outbound.model,
                content=answer.content,
                prompt_tokens=answer.prompt_tokens,
                completion_tokens=answer.completion_tokens,
                cost_usd=cost,
                latency_ms=_elapsed_ms(started),
            )
            self._settle(request.org_id, cost - estimate)
            self._log(request, reply, ok=True)
            if self.config.mode == "record":
                assert self.recordings is not None
                self.recordings.save(
                    Recording(
                        key=request_key(outbound),
                        model=reply.model,
                        content=reply.content,
                        prompt_tokens=reply.prompt_tokens,
                        completion_tokens=reply.completion_tokens,
                        cost_usd=str(reply.cost_usd),
                        latency_ms=reply.latency_ms,
                    )
                )
            return reply
        raise ModelUnavailableError(DOWN)

    def _ledger(self, session: Session, org_id: uuid.UUID) -> BudgetLedger:
        day = self.clock.now().date()
        session.execute(
            insert(BudgetLedger)
            .values(id=uuid.uuid4(), org_id=org_id, day=day, spent_usd=Decimal(0), calls=0)
            .on_conflict_do_nothing(index_elements=["org_id", "day"])
        )
        return session.scalars(
            select(BudgetLedger)
            .where(BudgetLedger.org_id == org_id, BudgetLedger.day == day)
            .with_for_update()
        ).one()

    def _spent(self, org_id: uuid.UUID) -> Decimal:
        with self.sessions() as session:
            spent = session.scalar(
                select(BudgetLedger.spent_usd).where(
                    BudgetLedger.org_id == org_id, BudgetLedger.day == self.clock.now().date()
                )
            )
        return Decimal(spent or 0)

    def _reserve(self, org_id: uuid.UUID, estimate: Decimal) -> None:
        """The budget pre-check. Refuses a call that could take the day past the cap."""
        with self.sessions.begin() as session:
            row = self._ledger(session, org_id)
            if row.spent_usd + estimate > self.config.daily_cap_usd:
                raise ModelUnavailableError(BUDGET)
            row.spent_usd += estimate
            row.calls += 1

    def _settle(self, org_id: uuid.UUID, difference: Decimal) -> None:
        with self.sessions.begin() as session:
            row = self._ledger(session, org_id)
            row.spent_usd += difference

    def _log(self, request: ModelRequest, reply: ModelReply, ok: bool) -> None:
        with self.sessions.begin() as session:
            session.add(
                ModelCall(
                    org_id=request.org_id,
                    message_id=request.message_id,
                    model=reply.model,
                    mode=self.config.mode,
                    prompt_tokens=reply.prompt_tokens,
                    completion_tokens=reply.completion_tokens,
                    cost_usd=reply.cost_usd,
                    latency_ms=reply.latency_ms,
                    ok=ok,
                )
            )


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


class _Unused:
    def complete(self, request: TransportRequest) -> TransportReply:
        raise AssertionError("no call is made while the model is off or has no key")
