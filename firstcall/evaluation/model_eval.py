"""The model eval: the corpus run with the model read, scored per method, with its gates,
its per-field choice and its cost from the call log.

A run that spends money records first and reports second. The record pass sends every
message (and clean twin) to the model once and saves each answer; the report is then made
from those recordings in replay mode. So the committed report and CI's rerun come from the
same answers and match byte for byte, and a recording that goes missing fails loudly.

Methods (see firstcall/core/model_read.py): rules-only, model-only, and combined, where the
rules go first and the model fills only what they missed or were unsure of. Model-only
needs an answer for every message, so its record pass covers the other methods too.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.core.model_read import FIELDS
from firstcall.corpus.generate import GOLD_DIR, TWINS
from firstcall.db import BudgetLedger, Lead, Message, ModelCall
from firstcall.evaluation.metrics import Rate
from firstcall.evaluation.report import with_floors
from firstcall.evaluation.run import (
    CONFIRM_SEED,
    FIELD_METHODS,
    REPO_ROOT,
    FixedClock,
    Result,
    field_methods_frozen,
    load_jsonl,
    run_corpus,
    score,
    throwaway_database,
)
from firstcall.model.gateway import Gateway, GatewayConfig, ReplayMissError
from firstcall.model.prices import Price
from firstcall.model.recordings import Recordings
from firstcall.model.synthetic import SyntheticIndex
from firstcall.model.transport import Transport
from firstcall.pipeline.model_read import UNREADABLE, ModelReader

RECORDINGS = REPO_ROOT / "evals" / "recordings"
# Cheapest first: rules cost nothing, combined calls the model only where the rules missed,
# model-only calls it for every field.
ORDER = ("rules", "combined", "model")
# The decision rule: within one point of the best, the cheaper method ships.
WITHIN = 0.01
MAX_UNREADABLE = 0.02
MAX_P95_MS = 6000
ALL_MODEL: Mapping[str, str] = dict.fromkeys(FIELDS, "model")
ALL_COMBINED: Mapping[str, str] = dict.fromkeys(FIELDS, "combined")


@dataclass(frozen=True)
class ModelRun:
    mode: str
    model: str
    budget_usd: Decimal
    recordings: Path = RECORDINGS
    transport: Transport | None = None
    key: str = ""
    prices: dict[str, Price] | None = None
    timeout_seconds: float = 8.0
    max_output_tokens: int = 400


class RecordingIncompleteError(Exception):
    """The record pass ended with messages that still have no recorded answer."""


def _gateway(sessions: sessionmaker[Session], run: ModelRun, cap: Decimal) -> Gateway:
    transport = run.transport
    if transport is None and run.mode in ("live", "record"):
        from firstcall.model.anthropic import AnthropicTransport

        transport = AnthropicTransport(run.key)
    config = GatewayConfig(
        mode=run.mode,
        model=run.model,
        has_key=run.mode == "replay" or bool(run.key) or run.transport is not None,
        off=False,
        daily_cap_usd=cap,
        timeout_seconds=run.timeout_seconds,
        max_output_tokens=run.max_output_tokens,
    )
    return Gateway(
        sessions,
        FixedClock(),
        config,
        transport=transport,
        recordings=Recordings(run.recordings) if run.mode in ("replay", "record") else None,
        synthetic=SyntheticIndex.from_gold() if run.mode == "record" else None,
        prices=run.prices,
    )


def _corpus(
    seed: int, limit: int | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    folder = GOLD_DIR / f"seed-{seed}"
    messages = load_jsonl(folder / "messages.jsonl")[:limit]
    labels = load_jsonl(folder / "labels.jsonl")[:limit]
    twins = {row["id"]: row["body"] for row in load_jsonl(folder / TWINS)}
    return messages, labels, twins


def _money(value: Decimal) -> str:
    return f"{value:.6f}"


def _percentile(values: list[int], share: float) -> int | None:
    """Nearest rank: the smallest value with at least `share` of the values at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * int(share * 100) // 100))
    return ordered[rank - 1]


def call_stats(session: Session) -> dict[str, Any]:
    """Calls, tokens, cost and latency from the call log, and cost per lead. Calls for clean
    twins (logged without a message) count toward the total spend but not toward the cost per
    message or per lead, which describe the message stream the pipeline would see."""
    calls = list(session.scalars(select(ModelCall)))
    answered = [c for c in calls if c.ok]
    on_messages = [c for c in calls if c.message_id is not None]
    answered_messages = {c.message_id for c in answered if c.message_id is not None}
    unreadable = (
        session.scalar(
            select(func.count()).select_from(Message).where(Message.model_unavailable == UNREADABLE)
        )
        or 0
    )
    messages = session.scalar(select(func.count()).select_from(Message)) or 0
    leads = session.scalar(select(func.count()).select_from(Lead)) or 0
    stream_cost = sum((c.cost_usd for c in on_messages), Decimal(0))
    latencies = [c.latency_ms for c in answered]
    return {
        "calls": len(calls),
        "failed_calls": len(calls) - len(answered),
        "prompt_tokens": sum(c.prompt_tokens for c in calls),
        "completion_tokens": sum(c.completion_tokens for c in calls),
        "cost_usd": _money(sum((c.cost_usd for c in calls), Decimal(0))),
        "cost_usd_messages": _money(stream_cost),
        "messages": messages,
        "leads": leads,
        "cost_per_message_usd": _money(stream_cost / messages) if messages else None,
        "cost_per_lead_usd": _money(stream_cost / leads) if leads else None,
        "unreadable": Rate(unreadable, len(answered_messages)).as_json(),
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies) if latencies else None,
        },
    }


def fully_right(results: list[Result], labels: list[dict[str, Any]]) -> dict[str, Any]:
    """Messages with every gold field right: for a lead, phone, email, name, item, agent
    present or not, category and team; for anything else, its category."""
    from firstcall.core.identity import normalize_name

    by_id = {r.id: r for r in results}
    hits = 0
    for gold in labels:
        result = by_id[gold["id"]]
        right = result.category == gold["category"]
        if right and gold["category"] == "lead":
            got = result.extracted
            right = (
                got.get("phone") == gold["phone"]
                and got.get("email") == gold["email"]
                and normalize_name(got.get("name")) == normalize_name(gold["name"])
                and got.get("item") == gold["item"]
                and (got.get("agent") is not None) == (gold["agent"] is not None)
                and result.team == gold["team"]
            )
        hits += right
    return Rate(hits, len(labels)).as_json()


def _run_method(
    seed: int,
    server_url: str,
    run: ModelRun,
    methods: Mapping[str, str] | None,
    limit: int | None,
    cap: Decimal | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Decimal]:
    """One method on one seed, under `cap` (the run budget when not given). Also returns the
    spend from the ledger, so a caller running several methods can keep one budget."""
    messages, labels, twins = _corpus(seed, limit)
    with throwaway_database(server_url) as sessions:
        reader = (
            None
            if methods is None
            else ModelReader(
                _gateway(sessions, run, run.budget_usd if cap is None else cap), methods
            )
        )
        results = run_corpus(sessions, messages, twins, reader)
        with sessions() as session:
            stats = call_stats(session)
        spent = _spent(sessions)
    scored = with_floors(score(results, labels))
    return scored, stats, fully_right(results, labels), spent


def _accuracy(scored: dict[str, Any], field: str, split: str = "all") -> float | None:
    part = scored["splits"][split]
    if field == "category":
        return part["category"]["accuracy"]["rate"]
    if field == "team":
        return part["placement"]["correct"]["rate"]
    return part["fields"][field]["rate"]


def choose_methods(report: dict[str, Any]) -> dict[str, str]:
    """The decision rule, fixed in the plan: per field, the most accurate method on all
    messages; within one point of the best, the cheaper one."""
    chosen: dict[str, str] = {}
    for field in FIELDS:
        accuracy = {m: _accuracy(report["methods"][m], field) for m in ORDER}
        known = [v for v in accuracy.values() if v is not None]
        if not known:
            chosen[field] = "rules"
            continue
        floor = max(known) - WITHIN - 1e-9
        chosen[field] = next(
            m for m in ORDER if (value := accuracy[m]) is not None and value >= floor
        )
    return chosen


def gates(scored: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    """The ADR-002 gates on the model-only run."""
    unreadable = stats["unreadable"]["rate"] or 0.0
    safety = [f for f in scored["floors"] if f["kind"] == "Safety"]
    latency = stats["latency_ms"]
    checks = {
        "unreadable": unreadable <= MAX_UNREADABLE,
        "safety_floors": all(f["met"] for f in safety),
        "latency_p95": latency["p95"] is not None and latency["p95"] <= MAX_P95_MS,
    }
    return {"checks": checks, "passed": all(checks.values())}


def pick(passes: list[dict[str, Any]]) -> str | None:
    """The ADR-002 pick: among models that pass every gate, the cheapest that is not clearly
    worse than the best. Clearly worse means the best's 95% interval on "message fully right"
    sits entirely above this model's score."""
    passing = [p for p in passes if p["gates"]["passed"]]
    if not passing:
        return None
    best = max(passing, key=lambda p: p["fully_right"]["rate"])
    low = best["fully_right"]["interval"][0]
    near = [p for p in passing if p["fully_right"]["rate"] >= low]
    return min(near, key=lambda p: p["price_per_call"])["model"]


def evaluate_models(
    seed: int, server_url: str, run: ModelRun, limit: int | None = None
) -> dict[str, Any]:
    """Rules-only, model-only and combined on one seed, with the gates, the per-field choice
    and the cost. Replay mode reads only recordings; a missing one raises ReplayMissError."""
    if seed == CONFIRM_SEED:
        raise ValueError("seed 13 is the confirm run; use evaluate_confirm")
    runs: dict[str, Mapping[str, str] | None] = {
        "rules": None,
        "model": ALL_MODEL,
        "combined": ALL_COMBINED,
    }
    methods: dict[str, Any] = {}
    calls: dict[str, Any] = {}
    right: dict[str, Any] = {}
    # Each method runs in its own throwaway database, so its own ledger; the run budget is
    # carried across them by hand, or a live run could spend it once per method.
    spent = Decimal(0)
    for name, chosen in runs.items():
        methods[name], calls[name], right[name], cost = _run_method(
            seed, server_url, run, chosen, limit, cap=max(run.budget_usd - spent, Decimal(0))
        )
        spent += cost
    model_calls = calls["model"]["calls"] or 1
    report: dict[str, Any] = {
        "seed": seed,
        "model": run.model,
        "messages": len(_corpus(seed, limit)[0]),
        "methods": methods,
        "calls": calls,
        "fully_right": right,
    }
    report["pass"] = {
        "model": run.model,
        "gates": gates(methods["model"], calls["model"]),
        "fully_right": right["model"],
        "price_per_call": float(Decimal(calls["model"]["cost_usd"]) / model_calls),
    }
    report["decision"] = choose_methods(report)
    return report


def evaluate_confirm(
    seed: int,
    server_url: str,
    run: ModelRun,
    methods: Mapping[str, str],
    limit: int | None = None,
    path: Path = FIELD_METHODS,
) -> dict[str, Any]:
    """The confirm run: the shipped per-field methods, once, after they are committed."""
    if seed == CONFIRM_SEED and not field_methods_frozen(path, REPO_ROOT):
        raise RuntimeError(
            "seed 13 is the confirm run; it runs once, after config/field_methods.yaml is committed"
        )
    scored, stats, right, _ = _run_method(seed, server_url, run, methods, limit)
    return {
        "seed": seed,
        "model": run.model,
        "messages": len(_corpus(seed, limit)[0]),
        "field_methods": dict(sorted(methods.items())),
        "shipped": scored,
        "calls": stats,
        "fully_right": right,
    }


def _spent(sessions: sessionmaker[Session]) -> Decimal:
    with sessions() as session:
        return Decimal(session.scalar(select(func.sum(BudgetLedger.spent_usd))) or 0)


def record(
    seed: int, server_url: str, run: ModelRun, limit: int | None = None, rounds: int = 3
) -> Decimal:
    """Record an answer for every message and clean twin of a seed, model-only, under the
    run budget. Returns the spend from the ledger (which keeps failed attempts' reservations,
    so it can read above the call log). A message whose calls failed is tried again in a
    later round; recordings already made are reused at no cost."""
    recording = replace(run, mode="record")
    messages, _, twins = _corpus(seed, limit)
    spent = Decimal(0)
    for _ in range(rounds):
        left = run.budget_usd - spent
        if left <= 0:
            break
        with throwaway_database(server_url) as sessions:
            reader = ModelReader(_gateway(sessions, recording, left), ALL_MODEL)
            run_corpus(sessions, messages, twins, reader)
            spent += _spent(sessions)
        try:
            _run_method(seed, server_url, replace(run, mode="replay"), ALL_MODEL, limit)
        except ReplayMissError:
            continue
        return spent
    raise RecordingIncompleteError(
        f"seed {seed}: some answers are still not recorded after {rounds} rounds "
        f"(${spent:.4f} spent of ${run.budget_usd})"
    )
