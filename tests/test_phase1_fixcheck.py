"""Regression tests for the fix check on the PR1b phase 1 review: mode-wide reread errors,
a message whose calls always fail, and a dotted capital I before a hidden HTML element."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.core import read
from firstcall.db import BudgetLedger, Message
from firstcall.model.synthetic import SyntheticIndex
from firstcall.model.transport import TransportError, TransportRequest
from firstcall.pipeline.model_read import ModelReader
from firstcall.pipeline.reread import MAX_TRANSPORT_FAILURES, SET_ASIDE, reread
from tests.conftest import FakeClock
from tests.helpers import email
from tests.model_fakes import FakeTransport, answer, gateway
from tests.test_model_read import UNSEEN, ModelPipeline

BO = email("<bo@inbox.example>", "Copperleaf, please.")


@pytest.fixture(autouse=True)
def no_ambient_model_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("FIRSTCALL_MODEL_"):
            monkeypatch.delenv(name)


@pytest.fixture
def pipe(sessions: sessionmaker[Session], clock: FakeClock) -> ModelPipeline:
    return ModelPipeline(sessions, clock)


def _fallback(pipe: ModelPipeline, raw: bytes = UNSEEN) -> None:
    pipe.with_model(FakeTransport(), off=True)
    assert pipe.deliver("inbox", raw).needs_reread is True


def _errors(pipe: ModelPipeline) -> list[int]:
    return [m.reread_errors for m in _messages(pipe)]


def _messages(pipe: ModelPipeline) -> list[Message]:
    with pipe.sessions() as session:
        return list(session.scalars(select(Message).order_by(Message.received_at)))


# ef71c1 x1: an error that belongs to the mode retires no message


def test_ef71c1_x1_a_replay_miss_ends_the_tick_without_counting(
    pipe: ModelPipeline, sessions: sessionmaker[Session], clock: FakeClock, tmp_path: Path
) -> None:
    _fallback(pipe)
    _fallback(pipe, BO)
    reader = ModelReader(gateway(sessions, clock, mode="replay", recordings=tmp_path))
    for _ in range(5):
        assert reread(sessions, reader) == {"halted": 1}
    assert _errors(pipe) == [0, 0]


def test_ef71c1_x1_a_non_synthetic_input_ends_the_tick_without_counting(
    pipe: ModelPipeline, sessions: sessionmaker[Session], clock: FakeClock, tmp_path: Path
) -> None:
    _fallback(pipe)
    transport = FakeTransport(default=answer())
    empty = SyntheticIndex(frozenset())
    record = gateway(sessions, clock, transport, "record", tmp_path, empty)
    reader = ModelReader(record)
    assert reread(sessions, reader) == {"halted": 1}
    assert _errors(pipe) == [0]
    assert transport.requests == []


# ef71c1 x2: a message whose calls always fail is set aside, and never blocks the queue


def _fails_for(text: str) -> FakeTransport:
    def respond(request: TransportRequest) -> str:
        if text in request.user:
            raise TransportError("this message always times out")
        return answer(name="Bo Lin")

    return FakeTransport(respond=respond)


def test_ef71c1_x2_a_message_that_always_fails_is_set_aside_and_others_go_on(
    pipe: ModelPipeline, sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    _fallback(pipe)
    _fallback(pipe, BO)
    transport = _fails_for("Who: Ana Ruiz")
    reader = ModelReader(gateway(sessions, clock, transport))
    for _ in range(MAX_TRANSPORT_FAILURES + 2):
        reread(sessions, reader)
    stuck, other = _messages(pipe)
    assert other.needs_reread is False
    failed = [r for r in transport.requests if "Who: Ana Ruiz" in r.user]
    assert len(failed) == MAX_TRANSPORT_FAILURES
    assert stuck.needs_reread is True
    assert stuck.model_unavailable == SET_ASIDE
    # Every failed attempt keeps its reservation: the ledger runs high, never low.
    with sessions() as session:
        spent = session.scalar(select(BudgetLedger.spent_usd)) or Decimal(0)
    assert spent > Decimal("0.0012")


# 2b32ab x1: a dotted capital I changes length when lowered


def test_2b32ab_x1_a_dotted_capital_i_before_a_title_keeps_positions() -> None:
    html = "<p>İstanbul İzmir</p><title>Contact form</title><p>Name: Ana Ruiz</p>"
    lines = [line for line in read._strip_tags(html).split("\n") if line]
    assert lines == ["İstanbul İzmir", "Name: Ana Ruiz"]
