"""The note eval (F32): every note script of a seed read through the gateway, scored by the
frozen note scorer, with the baselines beside it and the cost from the call log.

Each run uses a throwaway database, so the gateway's daily cap is this run's budget. A
script the model could not answer (budget spent, model down) leaves the run incomplete, and
an incomplete run writes no report. An answer that does not parse is scored as an answer
that fills nothing, and counted. Record mode saves every answer; replay reads them back, so
the committed report and CI's rerun come from the same answers.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.corpus.notes import LABELS, SCRIPTS, read_jsonl
from firstcall.db import BudgetLedger, ModelCall
from firstcall.evaluation.note_score import all_met, baselines, floors, score, score_part
from firstcall.evaluation.report import REPORTS
from firstcall.evaluation.run import REPO_ROOT, FixedClock, throwaway_database
from firstcall.model.gateway import Gateway, GatewayConfig, ReplayMissError
from firstcall.model.prices import Price
from firstcall.model.prompts import READ_NOTE, SYSTEM_PROMPTS
from firstcall.model.recordings import Recordings
from firstcall.model.synthetic import SyntheticIndex
from firstcall.model.transport import Transport
from firstcall.notes.reader import UNREADABLE, NoteReader

RECORDINGS = REPO_ROOT / "evals" / "recordings"
# The scripts are dated in the demo teams' local time.
ZONE = "America/New_York"


@dataclass(frozen=True)
class NoteRun:
    mode: str
    model: str
    budget_usd: Decimal
    recordings: Path = RECORDINGS
    transport: Transport | None = None
    key: str = ""
    prices: dict[str, Price] | None = None
    timeout_seconds: float = 8.0
    max_output_tokens: int = 600


def prompt_sha256() -> str:
    return hashlib.sha256(SYSTEM_PROMPTS[READ_NOTE].encode()).hexdigest()


def _gateway(
    sessions: sessionmaker[Session], run: NoteRun, synthetic: SyntheticIndex | None = None
) -> Gateway:
    """In record mode, `synthetic` replaces the committed corpus's index: the voice eval
    passes one built from the transcripts and clips it is reading."""
    transport = run.transport
    if transport is None and run.mode in ("live", "record"):
        from firstcall.model.anthropic import AnthropicTransport

        transport = AnthropicTransport(run.key)
    config = GatewayConfig(
        mode=run.mode,
        model=run.model,
        has_key=run.mode == "replay" or bool(run.key) or run.transport is not None,
        off=False,
        daily_cap_usd=run.budget_usd,
        timeout_seconds=run.timeout_seconds,
        max_output_tokens=run.max_output_tokens,
    )
    return Gateway(
        sessions,
        FixedClock(),
        config,
        transport=transport,
        recordings=Recordings(run.recordings) if run.mode in ("replay", "record") else None,
        synthetic=(synthetic or SyntheticIndex.from_gold()) if run.mode == "record" else None,
        prices=run.prices,
    )


def _calls(session: Session) -> dict[str, Any]:
    """The call log and the ledger. The ledger keeps failed attempts' reservations, so it
    can read above the logged cost."""
    row = session.execute(
        select(
            func.count(),
            func.count().filter(ModelCall.ok.is_(True)),
            func.coalesce(func.sum(ModelCall.prompt_tokens), 0),
            func.coalesce(func.sum(ModelCall.completion_tokens), 0),
            func.coalesce(func.sum(ModelCall.cost_usd), 0),
        )
    ).one()
    ledger = session.scalar(select(func.coalesce(func.sum(BudgetLedger.spent_usd), 0)))
    return {
        "calls": row[0],
        "ok": row[1],
        "failed": row[0] - row[1],
        "prompt_tokens": row[2],
        "completion_tokens": row[3],
        "cost_usd": f"{Decimal(row[4]):.6f}",
        "ledger_usd": f"{Decimal(ledger or 0):.6f}",
    }


def _json_prompt_answers(
    gateway: Gateway, reader: NoteReader, org: Any, scripts: list[dict[str, Any]]
) -> int:
    """How many of the run's answers were made with JSON asked for in the prompt, read from
    the recordings the run used. A resumed recording answers earlier scripts from file, so
    this run's transport never saw how they were made."""
    if gateway.recordings is None:
        return 0
    recordings = gateway.recordings.for_task(READ_NOTE)
    found = 0
    for script in scripts:
        request = reader.request(org, None, script["text"], script["note_date"], ZONE)
        recording = recordings.find(gateway.outbound(request))
        found += recording is not None and recording.output_mode != "json_schema"
    return found


def evaluate_notes(
    seed: int, server_url: str, run: NoteRun, limit: int | None = None
) -> dict[str, Any]:
    from firstcall.seed import seed as seed_world

    scripts = read_jsonl(seed, SCRIPTS)[:limit]
    labels = read_jsonl(seed, LABELS)[:limit]
    answers: dict[str, dict[str, Any] | None] = {}
    unanswered: list[str] = []
    with throwaway_database(server_url) as sessions:
        org = seed_world(sessions)
        gateway = _gateway(sessions, run)
        reader = NoteReader(gateway, detached=True)
        for script in scripts:
            outcome = reader.read(org, None, script["text"], script["note_date"], ZONE)
            if outcome.answer is not None:
                answers[script["id"]] = outcome.answer.as_json()
            elif outcome.reason == UNREADABLE:
                answers[script["id"]] = None
            else:
                unanswered.append(script["id"])
        with sessions() as session:
            calls = _calls(session)
        # The recordings say how every answer used was made, resumed ones included; the
        # transport's own count covers a live run that keeps no recordings.
        calls["json_prompt_calls"] = max(
            _json_prompt_answers(gateway, reader, org, scripts),
            int(getattr(gateway.transport, "json_prompt_calls", 0)),
        )
    others: dict[str, Any] = {}
    for name, given in baselines(scripts, labels).items():
        part = score_part({s["id"]: s["text"] for s in scripts}, labels, given)
        others[name] = {"met": all_met(part), "floors": floors(part)}
    return {
        "seed": seed,
        "model": run.model,
        "mode": run.mode,
        "prompt_sha256": prompt_sha256(),
        "complete": not unanswered,
        "unanswered": unanswered,
        "score": score(scripts, labels, answers),
        "baselines": others,
        "calls": calls,
        "structured_output": calls["json_prompt_calls"] == 0,
    }


class IncompleteRecordingError(Exception):
    """Recording left scripts unanswered, so the replay could not score them. Carries the
    record pass's summary, since its calls were paid for either way."""

    def __init__(self, record_pass: dict[str, Any], cause: str) -> None:
        self.record_pass = record_pass
        paid = record_pass
        super().__init__(
            f"recording is incomplete ({cause}); record pass: {paid['calls']} calls, "
            f"{paid['failed']} failed attempts, ${paid['cost_usd']} logged, "
            f"${paid['ledger_usd']} on the ledger; unanswered: "
            f"{', '.join(paid['unanswered']) or 'none'}"
        )


def record_and_report(
    seed: int,
    server_url: str,
    run: NoteRun,
    limit: int | None = None,
    on_recorded: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """The paid path: record every answer, then score from the recordings in replay mode.
    The report keeps the record pass's calls, failed attempts and ledger spend, which the
    replay cannot know. `on_recorded` gets that summary before the replay starts, so it is
    kept even when the replay fails."""
    recorded = evaluate_notes(seed, server_url, run, limit)
    record_pass = {**recorded["calls"], "unanswered": recorded["unanswered"]}
    if on_recorded is not None:
        on_recorded(record_pass)
    try:
        report = evaluate_notes(seed, server_url, replace(run, mode="replay"), limit)
    except ReplayMissError as error:
        raise IncompleteRecordingError(record_pass, str(error)) from error
    report["record_pass"] = record_pass
    report["structured_output"] = report["structured_output"] and recorded["structured_output"]
    return report


def write_record_pass(
    record_pass: dict[str, Any], seed: int, date: str, root: Path = REPORTS
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{date}-notes-seed-{seed}-record-pass.json"
    path.write_text(json.dumps(record_pass, indent=2, sort_keys=True) + "\n")
    return path


UNIT_NOTES = REPO_ROOT / "evals" / "gold" / "unit-notes.jsonl"
# A fixed date for the unit notes, so their recorded requests never change.
UNIT_DATE = "2026-03-02"


def read_unit_notes(server_url: str, run: NoteRun) -> dict[str, list[str]]:
    """The five fixed F31 notes (future, negation, plan, hypothetical, a colleague's call),
    each read once: the action kinds the model suggests for each, which should be none. An
    unreadable or missing answer raises, since these are read one at a time on purpose."""
    from firstcall.seed import seed as seed_world

    notes = [json.loads(line) for line in UNIT_NOTES.read_text().splitlines()]
    found: dict[str, list[str]] = {}
    with throwaway_database(server_url) as sessions:
        org = seed_world(sessions)
        reader = NoteReader(_gateway(sessions, run), detached=True)
        for note in notes:
            if note["kind"] == "action":
                continue
            outcome = reader.read(org, None, note["text"], UNIT_DATE, ZONE)
            if outcome.answer is None:
                raise ValueError(f"{note['id']}: no answer ({outcome.reason})")
            found[note["id"]] = [action.kind for action in outcome.answer.actions]
    return found


def _markdown(report: dict[str, Any]) -> str:
    whole = report["score"]["all"]
    lines = [
        f"# Note eval, seed {report['seed']}",
        "",
        f"Model `{report['model']}`, prompt sha256 `{report['prompt_sha256']}`.",
        f"{whole['scripts']} scripts, {whole['unreadable']} unreadable answers; "
        f"{report['calls']['calls']} calls, ${report['calls']['cost_usd']}.",
        "",
        "| Measure | Floor | All | Seen | Unseen | Met |",
        "|---|---|---|---|---|---|",
    ]
    seen, unseen = floors(report["score"]["seen"]), floors(report["score"]["unseen"])
    for row, seen_row, unseen_row in zip(whole["floors"], seen, unseen, strict=True):
        lines.append(
            f"| {row['measure']} | {row['floor']} | {row['value']} | {seen_row['value']} | "
            f"{unseen_row['value']} | {'yes' if row['met'] else 'no'} |"
        )
    paid = report.get("record_pass")
    if paid is not None:
        lines += [
            "",
            f"Record pass: {paid['calls']} calls, {paid['failed']} failed attempts, "
            f"${paid['cost_usd']} logged, ${paid['ledger_usd']} on the ledger.",
        ]
    if not report.get("structured_output", True):
        fell_back = max(report["calls"]["json_prompt_calls"],
                        paid["json_prompt_calls"] if paid else 0)  # fmt: skip
        lines += [
            "",
            f"**NOT on structured output: {fell_back} calls** fell back to JSON in the prompt.",
        ]
    lines += ["", "| Baseline | Meets every floor |", "|---|---|"]
    for name, found in report["baselines"].items():
        lines.append(f"| {name} | {'yes' if found['met'] else 'no'} |")
    return "\n".join(lines) + "\n"


def write_notes(report: dict[str, Any], date: str, root: Path = REPORTS) -> list[Path]:
    if not report["complete"]:
        raise ValueError(
            f"the run is incomplete ({len(report['unanswered'])} scripts unanswered); "
            "no report is written"
        )
    root.mkdir(parents=True, exist_ok=True)
    stem = root / f"{date}-notes-seed-{report['seed']}"
    data, text = stem.with_suffix(".json"), stem.with_suffix(".md")
    data.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    text.write_text(_markdown(report))
    return [data, text]
