"""Claude Haiku 5.5 is priced in two tiers: a prompt above 100,000 tokens is charged the
higher rates. Both the budget pre-check and the settle after the call must use them, or a
long email could spend past the cap."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import BudgetLedger, ModelCall
from firstcall.model.gateway import Gateway, ModelRequest
from firstcall.model.prices import load_prices
from firstcall.model.prompts import READ_MESSAGE
from firstcall.seed import seed
from tests.conftest import FakeClock
from tests.model_fakes import FakeTransport, answer, config

HAIKU = "claude-haiku-5-5"


def test_haiku_charges_the_long_prompt_rates_above_100k_prompt_tokens() -> None:
    price = load_prices()[HAIKU]
    assert price.cost(100_000, 1_000) == Decimal("0.0105")
    # Above the threshold the whole call is charged at $0.50 and $2.50 a million.
    assert price.cost(100_001, 1_000) == (
        Decimal("0.50") * 100_001 + Decimal("2.50") * 1_000
    ) / Decimal(1_000_000)


def test_a_flat_price_has_one_tier() -> None:
    price = load_prices()["openai/gpt-4o-mini"]
    assert price.cost(200_000, 0) == Decimal("0.03")


def _long_request(org_id: uuid.UUID, chars: int) -> ModelRequest:
    text = "x" * chars
    return ModelRequest(
        org_id=org_id,
        message_id=None,
        task=READ_MESSAGE,
        intake_text=text,
        context="Item list: Juniper Mesa\nTeam list: west (West)",
        schema={"type": "object"},
        raw=text.encode(),
    )


def test_the_pre_check_and_the_settle_both_price_a_long_prompt_at_the_long_rates(
    sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    org = seed(sessions)
    transport = FakeTransport(default=answer(), prompt_tokens=150_000, completion_tokens=100)
    gate = Gateway(sessions, clock, config(model=HAIKU), transport=transport, prices=load_prices())
    request = _long_request(org, 400_000)

    estimate = gate.estimate(gate.outbound(request))
    flat = (Decimal("0.100") * 133_334 + Decimal("0.500") * 400) / Decimal(1_000_000)
    assert estimate > 4 * flat

    gate.call(request)
    with sessions() as session:
        [call] = session.scalars(select(ModelCall)).all()
        spent = session.scalar(select(BudgetLedger.spent_usd))
    expected = (Decimal("0.50") * 150_000 + Decimal("2.50") * 100) / Decimal(1_000_000)
    assert call.cost_usd == expected
    # The ledger keeps six decimal places, so rounding the reservation can leave it a
    # millionth of a dollar high.
    assert spent is not None
    assert expected <= spent <= expected + Decimal("0.000001")
