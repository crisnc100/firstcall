"""The model eval's wiring: record then replay, the per-field decision rule, the pick rule, a
detached reader for clean twins, and a replay miss that stops the eval."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.corpus.generate import GOLD_DIR
from firstcall.db import ModelCall
from firstcall.evaluation.metrics import Rate
from firstcall.evaluation.model_eval import (
    ModelRun,
    choose_methods,
    evaluate_models,
    pick,
    record,
)
from firstcall.evaluation.model_report import write_model
from firstcall.model.gateway import ReplayMissError
from firstcall.model.synthetic import SyntheticIndex
from firstcall.pipeline.model_read import ModelReader, load_field_methods
from tests.conftest import FakeClock
from tests.model_fakes import MODEL, PRICES, FakeTransport, answer, gateway
from tests.test_gateway import request
from tests.test_model_read import UNSEEN, ModelPipeline


@pytest.fixture(autouse=True)
def no_ambient_model_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("FIRSTCALL_MODEL_"):
            monkeypatch.delenv(name)


def _rate(rate: float) -> dict[str, Any]:
    return {"hits": round(rate * 100), "total": 100, "rate": rate, "interval": [0.0, 1.0]}


def _scored(**rates: float) -> dict[str, Any]:
    fields = {f: _rate(rates.get(f, 0.5)) for f in ("phone", "email", "name", "item", "agent")}
    return {
        "splits": {
            "all": {
                "fields": fields,
                "category": {"accuracy": _rate(rates.get("category", 0.5))},
                "placement": {"correct": _rate(rates.get("team", 0.5))},
            }
        }
    }


# The per-field decision rule, fixed in the plan


def test_the_decision_rule_takes_the_most_accurate_and_the_cheaper_within_a_point() -> None:
    report = {
        "methods": {
            "rules": _scored(phone=0.99, email=0.80, name=0.70, category=0.60, team=0.90),
            "combined": _scored(phone=0.995, email=0.95, name=0.955, category=0.90, team=0.905),
            "model": _scored(phone=0.97, email=0.951, name=0.97, category=0.95, team=0.95),
        }
    }
    chosen = choose_methods(report)
    # Phone: combined is best by half a point, so the cheaper rules wins.
    assert chosen["phone"] == "rules"
    # Email: model leads combined by a tenth of a point; combined is cheaper.
    assert chosen["email"] == "combined"
    # Name: model leads by 1.5 points.
    assert chosen["name"] == "model"
    assert chosen["category"] == "model"
    assert chosen["team"] == "model"
    # A three-way tie goes to the cheapest.
    assert chosen["item"] == "rules"


# The pick rule from ADR-002


def _pass(model: str, price: float, fully_right: tuple[int, int], gates: bool) -> dict[str, Any]:
    hits, total = fully_right
    return {
        "model": model,
        "price_per_call": price,
        "gates": {"passed": gates},
        "fully_right": Rate(hits, total).as_json(),
    }


def test_the_pick_rule_takes_the_cheapest_passing_model_not_clearly_worse() -> None:
    reports = [
        _pass("cheap", 0.01, (300, 500), True),
        _pass("mid", 0.02, (430, 500), True),
        _pass("best", 0.05, (440, 500), True),
        _pass("failed", 0.001, (499, 500), False),
    ]
    # "cheap" is clearly worse than the best; "mid" overlaps it.
    assert pick(reports) == "mid"
    assert pick([_pass("only", 0.01, (400, 500), True)]) == "only"
    assert pick([_pass("only", 0.01, (400, 500), False)]) is None


# Gateway and reader pieces the eval needs


def test_record_mode_answers_from_an_existing_recording_without_a_call(
    sessions: sessionmaker[Session], clock: FakeClock, tmp_path: Path
) -> None:
    from firstcall.seed import seed

    org = seed(sessions)
    body = json.loads((GOLD_DIR / "seed-7" / "messages.jsonl").read_text().splitlines()[0])["body"]
    synthetic = SyntheticIndex.from_gold()
    first = FakeTransport(default=answer(name="Mario"))
    gateway(sessions, clock, first, "record", tmp_path, synthetic).call(
        request(org, body, body.encode())
    )
    again = FakeTransport(default=answer(name="Someone Else"))
    reply = gateway(sessions, clock, again, "record", tmp_path, synthetic).call(
        request(org, body, body.encode())
    )
    assert json.loads(reply.content)["name"] == "Mario"
    assert again.requests == []


def test_a_detached_reader_logs_its_calls_without_a_message(
    sessions: sessionmaker[Session], clock: FakeClock
) -> None:
    pipe = ModelPipeline(sessions, clock)
    pipe.reader = ModelReader(gateway(sessions, clock, FakeTransport(default=answer()))).detached()
    pipe.deliver("inbox", UNSEEN)
    with sessions() as session:
        assert [c.message_id for c in session.scalars(select(ModelCall))] == [None]


def test_field_methods_default_to_combined_when_the_file_is_absent(tmp_path: Path) -> None:
    methods = load_field_methods(tmp_path / "missing.yaml")
    assert set(methods.values()) == {"combined"}
    chosen = tmp_path / "field_methods.yaml"
    chosen.write_text(
        "methods:\n  phone: rules\n  email: rules\n  name: model\n  item: combined\n"
        "  agent: rules\n  category: model\n  team: combined\n"
    )
    assert load_field_methods(chosen)["name"] == "model"


def test_field_methods_with_a_missing_or_unknown_field_are_refused(tmp_path: Path) -> None:
    bad = tmp_path / "field_methods.yaml"
    bad.write_text("methods:\n  phone: rules\n")
    with pytest.raises(ValueError, match="field_methods"):
        load_field_methods(bad)
    bad.write_text(
        "methods:\n  phone: guess\n  email: rules\n  name: model\n  item: combined\n"
        "  agent: rules\n  category: model\n  team: combined\n"
    )
    with pytest.raises(ValueError, match="field_methods"):
        load_field_methods(bad)


# Record, then replay: the report is the same bytes, and a miss stops the eval


def _run(mode: str, folder: Path, transport: FakeTransport | None = None) -> ModelRun:
    return ModelRun(
        mode=mode, model=MODEL, budget_usd=Decimal("1.00"), recordings=folder,
        transport=transport, prices={MODEL: _price()},
    )  # fmt: skip


def _price() -> Any:
    return PRICES[MODEL]


def test_a_record_pass_then_replay_gives_the_same_report(database_url: str, tmp_path: Path) -> None:
    folder = tmp_path / "recordings"
    transport = FakeTransport(default=answer(category="lead"), delay_seconds=0.001)
    spent = record(7, database_url, _run("record", folder, transport), limit=24)
    assert spent > Decimal(0)
    assert transport.requests
    calls = len(transport.requests)

    replayed = evaluate_models(7, database_url, _run("replay", folder), limit=24)
    again = evaluate_models(7, database_url, _run("replay", folder), limit=24)
    assert replayed == again
    assert len(transport.requests) == calls
    assert set(replayed["methods"]) == {"rules", "model", "combined"}
    assert replayed["calls"]["model"]["calls"] > 0
    assert replayed["calls"]["rules"]["calls"] == 0
    written = write_model(replayed, "2026-01-01", tmp_path / "reports")
    rewritten = write_model(again, "2026-01-01", tmp_path / "again")
    assert [p.read_bytes() for p in written] == [p.read_bytes() for p in rewritten]


def test_a_replay_miss_stops_the_eval(database_url: str, tmp_path: Path) -> None:
    with pytest.raises(ReplayMissError):
        evaluate_models(7, database_url, _run("replay", tmp_path), limit=3)
