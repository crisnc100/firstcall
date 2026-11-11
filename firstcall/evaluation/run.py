"""The rules-only eval: run a seed's corpus through the real pipeline on a throwaway
database, then score every result against gold.

Messages are stored and processed one at a time in corpus order, as one worker would. The
report holds no timings or ids, so a second run gives the same bytes.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from firstcall.core.delivery import sha256
from firstcall.core.identity import normalize_name
from firstcall.corpus.generate import GOLD_DIR, TWINS
from firstcall.db import TERMINAL_STATUSES, Message, Source, Team
from firstcall.evaluation.metrics import Rate
from firstcall.intake.store import accept
from firstcall.pipeline.jobs import run_one
from firstcall.pipeline.process import process
from firstcall.seed import seed as seed_demo

FIELDS = ("phone", "email", "name", "item", "agent")
SPLITS = ("seen", "unseen", "all")
HARD_NEGATIVE_KINDS = (
    "shared_agent_phone",
    "household_phone",
    "portal_relay_sender",
    "call_tracking_number",
    "phone_email_conflict",
)
CONFIRM_SEED = 13
FIELD_METHODS = Path(__file__).resolve().parents[2] / "config" / "field_methods.yaml"


class FixedClock:
    """Event ids and leases need a time; a fixed one keeps the run repeatable."""

    def now(self) -> datetime:
        return datetime(2026, 3, 2, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Result:
    id: str
    status: str
    reason: str
    category: str | None
    extracted: dict[str, Any]
    team: str | None
    lead: str | None
    created: bool
    # The same message without its attack sentence, processed against the same database state.
    twin: Result | None = None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


@contextmanager
def throwaway_database(server_url: str) -> Iterator[sessionmaker[Session]]:
    from alembic import command
    from alembic.config import Config

    name = f"firstcall_eval_{uuid.uuid4().hex[:12]}"
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(server_url).set(database=name).render_as_string(hide_password=False)
    engine = create_engine(url)
    try:
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        config.set_main_option(
            "script_location", str(Path(__file__).resolve().parents[2] / "migrations")
        )
        config.attributes["url"] = url
        command.upgrade(config, "head")
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _twin(
    sessions: sessionmaker[Session],
    source: Source,
    row: dict[str, Any],
    body: str,
    teams: dict[uuid.UUID, str],
    clock: FixedClock,
) -> Result:
    """Process the clean twin inside a transaction that is rolled back, just before the real
    message, so it sees exactly the leads the real message sees and leaves nothing behind."""
    raw = body.encode()
    with sessions() as session:
        try:
            twin = Message(
                id=uuid.uuid4(),
                org_id=source.org_id,
                source_id=source.id,
                content_type=row["content_type"],
                raw=raw,
                body_sha256=sha256(raw),
                status="received",
            )
            session.add(twin)
            session.flush()
            status = process(session, twin.id, clock)
            result = Result(
                id=row["id"],
                status=status,
                reason=twin.reason or "",
                category=twin.category,
                extracted=dict(twin.extracted or {}),
                team=teams.get(twin.team_id) if twin.team_id else None,
                lead=None,
                created=False,
            )
        finally:
            session.rollback()
    return result


def run_corpus(
    sessions: sessionmaker[Session],
    messages: list[dict[str, Any]],
    twins: dict[str, str] | None = None,
) -> list[Result]:
    seed_demo(sessions)
    clock = FixedClock()
    with sessions() as session:
        sources = {s.slug: s for s in session.scalars(select(Source))}
        teams = {t.id: t.slug for t in session.scalars(select(Team))}
    results: list[Result] = []
    for row in messages:
        source = sources[row["source"]]
        twin_body = (twins or {}).get(row["id"])
        twin = _twin(sessions, source, row, twin_body, teams, clock) if twin_body else None
        body = row["body"].encode()
        accepted = accept(sessions, source, body, row["content_type"], row["headers"])
        while run_one(sessions, clock):
            pass
        with sessions() as session:
            message = session.get_one(Message, accepted.message_id)
            created = message.status == "lead_created"
            results.append(
                Result(
                    id=row["id"],
                    status="duplicate_delivery"
                    if accepted.status != "received"
                    else message.status,
                    reason=message.reason or "",
                    category=message.category,
                    extracted=dict(message.extracted or {}),
                    team=teams.get(message.team_id) if message.team_id else None,
                    lead=str(message.lead_id) if message.lead_id else None,
                    created=created and accepted.status == "received",
                    twin=twin,
                )
            )
    return results


def _attacked_value(result: Result, field: str) -> str | None:
    if field == "category":
        return result.category
    if field == "team":
        return result.team
    value = result.extracted.get(field)
    return normalize_name(value) if field == "name" else value


def _field_ok(result: Result, gold: dict[str, Any], field: str) -> bool:
    if field == "agent":
        return (result.extracted.get("agent") is not None) == (gold["agent"] is not None)
    if field == "name":
        return normalize_name(result.extracted.get("name")) == normalize_name(gold["name"])
    return result.extracted.get(field) == gold[field]


def score(results: list[Result], labels: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {r.id: r for r in results}
    gold_lead_to_system: dict[str, str] = {}
    for label in labels:
        result = by_id[label["id"]]
        if label["lead"] and label["case"] != "repeat" and result.created and result.lead:
            gold_lead_to_system.setdefault(label["lead"], result.lead)

    def subset(split: str) -> list[dict[str, Any]]:
        if split == "all":
            return labels
        return [g for g in labels if g["seen"] == (split == "seen")]

    report: dict[str, Any] = {"splits": {}}
    for split in SPLITS:
        rows = subset(split)
        leads = [g for g in rows if g["category"] == "lead"]
        part: dict[str, Any] = {"messages": len(rows), "lead_messages": len(leads)}
        part["fields"] = {
            f: Rate(sum(_field_ok(by_id[g["id"]], g, f) for g in leads), len(leads)).as_json()
            for f in FIELDS
        }
        confusion: Counter[str] = Counter()
        for g in rows:
            confusion[f"{g['category']}->{by_id[g['id']].category}"] += 1
        part["category"] = {
            "accuracy": Rate(
                sum(by_id[g["id"]].category == g["category"] for g in rows), len(rows)
            ).as_json(),
            "confusion": dict(sorted(confusion.items())),
            "lead_as_not_lead": sum(by_id[g["id"]].category != "lead" for g in leads),
            "spam_as_lead": Rate(
                sum(by_id[g["id"]].category == "lead" for g in rows if g["category"] == "spam"),
                sum(1 for g in rows if g["category"] == "spam"),
            ).as_json(),
        }
        knowable = [g for g in leads if g["team"] is not None]
        ambiguous = [g for g in rows if g["case"] == "ambiguous"]
        part["placement"] = {
            "correct": Rate(
                sum(by_id[g["id"]].team == g["team"] for g in knowable), len(knowable)
            ).as_json(),
            "wrong_team": Rate(
                sum(by_id[g["id"]].team not in (None, g["team"]) for g in knowable), len(knowable)
            ).as_json(),
            "unassigned": Rate(
                sum(by_id[g["id"]].team is None for g in knowable), len(knowable)
            ).as_json(),
            "ambiguous_placed": {
                "count": sum(by_id[g["id"]].team is not None for g in ambiguous),
                "of": len(ambiguous),
            },
        }
        repeats = [g for g in rows if g["case"] == "repeat"]
        attached_right = 0
        attached_wrong = 0
        for g in repeats:
            result = by_id[g["id"]]
            expected = gold_lead_to_system.get(g["lead"])
            if result.lead and not result.created:
                if result.lead == expected:
                    attached_right += 1
                else:
                    attached_wrong += 1
        hard: dict[str, dict[str, int]] = {}
        for kind in HARD_NEGATIVE_KINDS:
            of_kind = [g for g in rows if kind in g["hard_negatives"]]
            merged = sum(1 for g in of_kind if by_id[g["id"]].lead and not by_id[g["id"]].created)
            hard[kind] = {"wrong_merges": merged, "of": len(of_kind)}
        other_new = [g for g in leads if g["case"] != "repeat" and not g["hard_negatives"]]
        part["matching"] = {
            "recall": Rate(attached_right, len(repeats)).as_json(),
            "wrong_attaches": {"count": attached_wrong, "of": len(repeats)},
            "hard_negatives": hard,
            # A message can carry two hard-negative kinds; the total counts it once.
            "hard_negative_total": {
                "wrong_merges": sum(
                    1
                    for g in rows
                    if g["hard_negatives"] and by_id[g["id"]].lead and not by_id[g["id"]].created
                ),
                "of": sum(1 for g in rows if g["hard_negatives"]),
            },
            "other_new_leads_merged": {
                "count": sum(
                    1 for g in other_new if by_id[g["id"]].lead and not by_id[g["id"]].created
                ),
                "of": len(other_new),
            },
        }
        injected = [g for g in rows if g["injection"]]
        taken: Counter[str] = Counter()
        in_output: Counter[str] = Counter()
        cases: Counter[str] = Counter()
        for g in injected:
            attacked = g["injection"]["field"]
            cases[attacked] += 1
            value = g["injection"]["value"]
            want = normalize_name(value) if attacked == "name" else value
            result = by_id[g["id"]]
            got = _attacked_value(result, attacked)
            if got != want:
                continue
            in_output[attacked] += 1
            # A hit needs the attack to have caused it. Without a twin that cannot be shown,
            # so the case counts as a hit.
            if result.twin is None or _attacked_value(result.twin, attacked) != got:
                taken[attacked] += 1
        part["injection"] = {
            "taken": sum(taken.values()),
            "attacker_value_in_output": sum(in_output.values()),
            "of": len(injected),
            "by_field": {
                f: {"taken": taken[f], "attacker_value_in_output": in_output[f], "of": cases[f]}
                for f in sorted(cases)
            },
        }
        agent_cases = [g for g in leads if g["agent"]]
        leaks = 0
        for g in agent_cases:
            got = by_id[g["id"]].extracted
            agent = g["agent"]
            if (agent["phone"] and got.get("phone") == agent["phone"]) or (
                agent["email"] and got.get("email") == agent["email"]
            ):
                leaks += 1
        part["agent_contact_leak"] = {"count": leaks, "of": len(agent_cases)}
        statuses = Counter(by_id[g["id"]].status for g in rows)
        part["statuses"] = dict(sorted(statuses.items()))
        part["not_terminal"] = sum(
            1
            for g in rows
            if by_id[g["id"]].status not in (*TERMINAL_STATUSES, "duplicate_delivery")
        )
        part["missing_reason"] = sum(
            1
            for g in rows
            if by_id[g["id"]].status != "duplicate_delivery" and not by_id[g["id"]].reason
        )
        report["splits"][split] = part
    return report


def evaluate(seed: int, server_url: str) -> dict[str, Any]:
    if seed == CONFIRM_SEED and not FIELD_METHODS.exists():
        raise RuntimeError(
            "seed 13 is the confirm run; it runs once, after config/field_methods.yaml is committed"
        )
    folder = GOLD_DIR / f"seed-{seed}"
    messages = load_jsonl(folder / "messages.jsonl")
    labels = load_jsonl(folder / "labels.jsonl")
    twins = {row["id"]: row["body"] for row in load_jsonl(folder / TWINS)}
    with throwaway_database(server_url) as sessions:
        results = run_corpus(sessions, messages, twins)
    scored = score(results, labels)
    scored["seed"] = seed
    scored["method"] = "rules-only"
    scored["model_calls"] = 0
    return scored
