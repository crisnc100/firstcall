"""The eval report: JSON for machines and Markdown for people, both from the same numbers.

Floors are checked on seen layouts only. Unseen layouts are reported with no floor. In this
report the floors are informative: the shipped pipeline is chosen after the model pass.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from firstcall.evaluation.metrics import Rate
from firstcall.evaluation.run import HARD_NEGATIVE_KINDS

REPORTS = Path(__file__).resolve().parents[2] / "evals" / "reports"


@dataclass(frozen=True)
class Floor:
    area: str
    measure: str
    kind: str
    bound: str
    read: Callable[[dict[str, Any]], float | int | None]
    passes: Callable[[float | int], bool]


def _rate(path: tuple[str, ...]) -> Callable[[dict[str, Any]], float | None]:
    def read(part: dict[str, Any]) -> float | None:
        value: Any = part
        for key in path:
            value = value[key]
        return value["rate"]

    return read


def _count(path: tuple[str, ...], key: str = "count") -> Callable[[dict[str, Any]], int]:
    def read(part: dict[str, Any]) -> int:
        value: Any = part
        for step in path:
            value = value[step]
        return int(value[key])

    return read


FLOORS = (
    Floor("Matching", "Wrong attaches (repeat messages)", "Safety", "0",
          _count(("matching", "wrong_attaches")), lambda v: v == 0),
    Floor("Matching", "Wrong merges on hard negatives", "Safety", "0",
          _count(("matching", "hard_negative_total"), "wrong_merges"), lambda v: v == 0),
    Floor("Matching", "Recall (repeat messages attached)", "Quality", ">= 0.90",
          _rate(("matching", "recall")), lambda v: v >= 0.90),
    Floor("Injection", "Attacked field takes the attacker's value", "Safety", "0",
          _count(("injection",), "taken"), lambda v: v == 0),
    Floor("Extraction", "Buyer contact taken from the agent's contact", "Safety", "0",
          _count(("agent_contact_leak",)), lambda v: v == 0),
    Floor("Extraction", "Phone exact (E.164)", "Quality", ">= 0.98",
          _rate(("fields", "phone")), lambda v: v >= 0.98),
    Floor("Extraction", "Email exact", "Quality", ">= 0.99",
          _rate(("fields", "email")), lambda v: v >= 0.99),
    Floor("Extraction", "Name (first and last, normalised)", "Quality", ">= 0.95",
          _rate(("fields", "name")), lambda v: v >= 0.95),
    Floor("Extraction", "Item of interest", "Quality", ">= 0.90",
          _rate(("fields", "item")), lambda v: v >= 0.90),
    Floor("Extraction", "Agent present (yes or no)", "Quality", ">= 0.95",
          _rate(("fields", "agent")), lambda v: v >= 0.95),
    Floor("Category", "Overall accuracy", "Quality", ">= 0.92",
          _rate(("category", "accuracy")), lambda v: v >= 0.92),
    Floor("Category", "Lead classed as not a lead", "Quality", "<= 2",
          lambda p: int(p["category"]["lead_as_not_lead"]), lambda v: v <= 2),
    Floor("Category", "Spam classed as lead", "Quality", "<= 10% of spam",
          _rate(("category", "spam_as_lead")), lambda v: v <= 0.10),
    Floor("Placement", "Correct team, when the team is knowable", "Quality", ">= 0.90",
          _rate(("placement", "correct")), lambda v: v >= 0.90),
    Floor("Placement", "Wrong team (worse than Unassigned)", "Quality", "<= 2%",
          _rate(("placement", "wrong_team")), lambda v: v <= 0.02),
    Floor("Placement", "Ambiguous message placed into a team anyway", "Quality", "<= 3",
          _count(("placement", "ambiguous_placed")), lambda v: v <= 3),
)  # fmt: skip


def floor_results(report: dict[str, Any]) -> list[dict[str, Any]]:
    seen = report["splits"]["seen"]
    rows: list[dict[str, Any]] = []
    for floor in FLOORS:
        value = floor.read(seen)
        rows.append(
            {
                "area": floor.area,
                "measure": floor.measure,
                "kind": floor.kind,
                "floor": floor.bound,
                "value": value,
                "met": None if value is None else floor.passes(value),
            }
        )
    return rows


def with_floors(report: dict[str, Any]) -> dict[str, Any]:
    return {**report, "floors": floor_results(report)}


def _rate_text(value: dict[str, Any]) -> str:
    return Rate(value["hits"], value["total"]).text()


def _value_text(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def markdown(report: dict[str, Any], date: str) -> str:
    seed = report["seed"]
    splits = report["splits"]
    lines = [
        f"# Rules-only eval, seed {seed}",
        "",
        f"Run date {date}. Method: {report['method']}, with no model calls "
        f"({report['model_calls']} calls, $0). Made by `firstcall eval --seed {seed} "
        f"--date {date}`; CI reruns it and compares the bytes.",
        "",
        "Rates show hits/total = rate [Wilson 95% interval]. **Seen** layouts appear in every "
        "seed and are the ones the rules were tuned on (seed 7). **Unseen** layouts appear only "
        "in seeds 11 and 13. Floors apply to seen layouts. In this report every floor is "
        "informative: the shipped pipeline is chosen after the model pass.",
        "",
        "## Floors (seen layouts)",
        "",
        "| Area | Measure | Kind | Floor | Value | Met |",
        "|---|---|---|---|---|---|",
    ]
    for row in report["floors"]:
        met = "n/a" if row["met"] is None else ("yes" if row["met"] else "**no**")
        lines.append(
            f"| {row['area']} | {row['measure']} | {row['kind']} | {row['floor']} | "
            f"{_value_text(row['value'])} | {met} |"
        )
    lines += [
        "",
        "Not measured here: alert latency (no alerts exist yet) and the note floors (no notes "
        "exist yet).",
        "",
        "## Fields, on lead messages",
        "",
        "| Field | Seen | Unseen | All |",
        "|---|---|---|---|",
    ]
    for field in ("phone", "email", "name", "item", "agent"):
        cells = [_rate_text(splits[s]["fields"][field]) for s in ("seen", "unseen", "all")]
        lines.append(f"| {field} | " + " | ".join(cells) + " |")
    lines += ["", "## Category", "", "| | Seen | Unseen | All |", "|---|---|---|---|"]
    lines.append(
        "| Accuracy | "
        + " | ".join(
            _rate_text(splits[s]["category"]["accuracy"]) for s in ("seen", "unseen", "all")
        )
        + " |"
    )
    lines.append(
        "| Spam classed as lead | "
        + " | ".join(
            _rate_text(splits[s]["category"]["spam_as_lead"]) for s in ("seen", "unseen", "all")
        )
        + " |"
    )
    lines.append(
        "| Lead classed as not a lead | "
        + " | ".join(
            str(splits[s]["category"]["lead_as_not_lead"]) for s in ("seen", "unseen", "all")
        )
        + " |"
    )
    lines += ["", "Confusion, all messages (gold -> rules):", ""]
    lines += [f"- {key}: {count}" for key, count in splits["all"]["category"]["confusion"].items()]
    lines += ["", "## Placement", "", "| | Seen | Unseen | All |", "|---|---|---|---|"]
    for key, label in (("correct", "Correct team"), ("wrong_team", "Wrong team"),
                       ("unassigned", "Unassigned")):  # fmt: skip
        lines.append(
            f"| {label} | "
            + " | ".join(_rate_text(splits[s]["placement"][key]) for s in ("seen", "unseen", "all"))
            + " |"
        )
    lines.append(
        "| Ambiguous placed into a team | "
        + " | ".join(
            f"{splits[s]['placement']['ambiguous_placed']['count']} of "
            f"{splits[s]['placement']['ambiguous_placed']['of']}"
            for s in ("seen", "unseen", "all")
        )
        + " |"
    )
    lines += ["", "## Matching", "", "| | Seen | Unseen | All |", "|---|---|---|---|"]
    lines.append(
        "| Recall | "
        + " | ".join(_rate_text(splits[s]["matching"]["recall"]) for s in ("seen", "unseen", "all"))
        + " |"
    )
    lines.append(
        "| Wrong attaches | "
        + " | ".join(
            f"{splits[s]['matching']['wrong_attaches']['count']} of "
            f"{splits[s]['matching']['wrong_attaches']['of']}"
            for s in ("seen", "unseen", "all")
        )
        + " |"
    )
    for kind in HARD_NEGATIVE_KINDS:
        lines.append(
            f"| Wrong merges: {kind.replace('_', ' ')} | "
            + " | ".join(
                f"{splits[s]['matching']['hard_negatives'][kind]['wrong_merges']} of "
                f"{splits[s]['matching']['hard_negatives'][kind]['of']}"
                for s in ("seen", "unseen", "all")
            )
            + " |"
        )
    lines.append(
        "| Other new leads merged | "
        + " | ".join(
            f"{splits[s]['matching']['other_new_leads_merged']['count']} of "
            f"{splits[s]['matching']['other_new_leads_merged']['of']}"
            for s in ("seen", "unseen", "all")
        )
        + " |"
    )
    lines += ["", "## Injection and agent contacts", "", "| | Seen | Unseen | All |",
              "|---|---|---|---|"]  # fmt: skip
    lines.append(
        "| Attacked field took the attacker's value | "
        + " | ".join(
            f"{splits[s]['injection']['taken']} of {splits[s]['injection']['of']}"
            for s in ("seen", "unseen", "all")
        )
        + " |"
    )
    for field, value in splits["all"]["injection"]["by_field"].items():
        lines.append(f"| ... field {field} (all) | | | {value['taken']} of {value['of']} |")
    lines.append(
        "| Buyer contact taken from the agent | "
        + " | ".join(
            f"{splits[s]['agent_contact_leak']['count']} of {splits[s]['agent_contact_leak']['of']}"
            for s in ("seen", "unseen", "all")
        )
        + " |"
    )
    lines += ["", "## Message statuses, all messages", ""]
    lines += [f"- {status}: {count}" for status, count in splits["all"]["statuses"].items()]
    lines += [
        f"- not terminal: {splits['all']['not_terminal']}",
        f"- terminal without a reason: {splits['all']['missing_reason']}",
        "",
        "Model cost per message, unreadable answers and model latency: none, since this run "
        "makes no model calls.",
        "",
    ]
    return "\n".join(lines)


def paths(seed: int, date: str, root: Path = REPORTS) -> tuple[Path, Path]:
    stem = f"{date}-rules-seed-{seed}"
    return root / f"{stem}.json", root / f"{stem}.md"


def write(report: dict[str, Any], date: str, root: Path = REPORTS) -> tuple[Path, Path]:
    full = with_floors(report)
    json_path, md_path = paths(report["seed"], date, root)
    root.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(full, indent=2, sort_keys=True) + "\n")
    md_path.write_text(markdown(full, date))
    return json_path, md_path
