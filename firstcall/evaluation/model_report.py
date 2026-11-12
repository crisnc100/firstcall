"""The model eval reports, JSON and Markdown from the same numbers.

- `<date>-model-seed-<N>`: rules-only, model-only and combined on seed 7 or 11, the
  ADR-002 gates, the per-field choice, and cost from the call log.
- `<date>-confirm-seed-13`: the shipped per-field methods on the confirm seed, once.

Both are made from recorded answers, so CI reruns them in replay mode and compares bytes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from firstcall.core.model_read import FIELDS
from firstcall.evaluation.metrics import Rate
from firstcall.evaluation.model_eval import ORDER
from firstcall.evaluation.report import REPORTS

SPLITS = ("seen", "unseen", "all")
LABELS = {
    "phone": "Phone exact",
    "email": "Email exact",
    "name": "Name",
    "item": "Item",
    "agent": "Agent present",
    "category": "Category (all messages)",
    "team": "Team, when knowable",
}


def model_paths(seed: int, date: str, root: Path = REPORTS) -> tuple[Path, Path]:
    stem = f"{date}-model-seed-{seed}"
    return root / f"{stem}.json", root / f"{stem}.md"


def confirm_paths(seed: int, date: str, root: Path = REPORTS) -> tuple[Path, Path]:
    stem = f"{date}-confirm-seed-{seed}"
    return root / f"{stem}.json", root / f"{stem}.md"


def _rate(value: dict[str, Any]) -> str:
    return Rate(value["hits"], value["total"]).text()


def _field_rate(scored: dict[str, Any], field: str, split: str) -> str:
    part = scored["splits"][split]
    if field == "category":
        return _rate(part["category"]["accuracy"])
    if field == "team":
        return _rate(part["placement"]["correct"])
    return _rate(part["fields"][field])


def _value(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def _floors(columns: dict[str, dict[str, Any]]) -> list[str]:
    names = list(columns)
    lines = [
        "| Area | Measure | Kind | Floor | " + " | ".join(names) + " |",
        "|---|---|---|---|" + "---|" * len(names),
    ]
    first = next(iter(columns.values()))["floors"]
    for index, row in enumerate(first):
        cells = []
        for name in names:
            floor = columns[name]["floors"][index]
            met = "" if floor["met"] is None else (" (met)" if floor["met"] else " (**missed**)")
            cells.append(_value(floor["value"]) + met)
        lines.append(
            f"| {row['area']} | {row['measure']} | {row['kind']} | {row['floor']} | "
            + " | ".join(cells)
            + " |"
        )
    return lines


def _calls(columns: dict[str, dict[str, Any]]) -> list[str]:
    names = list(columns)
    rows = (
        ("Calls (failed)", lambda c: f"{c['calls']} ({c['failed_calls']})"),
        ("Prompt / completion tokens",
         lambda c: f"{c['prompt_tokens']} / {c['completion_tokens']}"),
        ("Spend, all calls", lambda c: f"${c['cost_usd']}"),
        ("Spend on the message stream", lambda c: f"${c['cost_usd_messages']}"),
        ("Cost per message", lambda c: f"${c['cost_per_message_usd'] or '0'}"),
        ("Cost per lead", lambda c: f"${c['cost_per_lead_usd'] or '0'}"),
        ("Unreadable answers", lambda c: _rate(c["unreadable"])),
        ("Latency p50 / p95 / max (ms)", lambda c: " / ".join(
            _value(c["latency_ms"][k]) for k in ("p50", "p95", "max"))),
    )  # fmt: skip
    lines = ["| | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for label, read in rows:
        lines.append(f"| {label} | " + " | ".join(read(columns[n]) for n in names) + " |")
    return lines


def model_markdown(report: dict[str, Any], date: str) -> str:
    seed = report["seed"]
    methods = report["methods"]
    gate = report["pass"]["gates"]
    lines = [
        f"# Model eval, seed {seed}",
        "",
        f"Run date {date}. Model `{report['model']}`, {report['messages']} messages. Every "
        "model answer here was recorded from a live call on the Firstcall key and replayed "
        f"from `evals/recordings/`. Made by `firstcall eval --seed {seed} --mode record "
        f"--date {date}`; CI reruns it with `--mode replay` and compares the bytes.",
        "",
        "Rates show hits/total = rate [Wilson 95% interval]. **Seen** layouts appear in every "
        "seed; **unseen** layouts only in seeds 11 and 13. Floors apply to seen layouts.",
        "",
        "Methods: **rules** is the rules read alone; **model** takes every field from the "
        "model; **combined** keeps a rules value unless the rules found nothing or were "
        "unsure, and then takes the model's.",
        "",
        "## ADR-002 gates (model-only run)",
        "",
        "| Gate | Bound | Value | Met |",
        "|---|---|---|---|",
        f"| Unreadable answers | <= 2% | {_rate(report['calls']['model']['unreadable'])} | "
        f"{'yes' if gate['checks']['unreadable'] else '**no**'} |",
        f"| Every safety floor | met | see floors | "
        f"{'yes' if gate['checks']['safety_floors'] else '**no**'} |",
        f"| Model read latency p95 | <= 6,000 ms | "
        f"{_value(report['calls']['model']['latency_ms']['p95'])} ms | "
        f"{'yes' if gate['checks']['latency_p95'] else '**no**'} |",
        "",
        f"All gates passed: **{'yes' if gate['passed'] else 'no'}**. Message fully right "
        f"(model-only): {_rate(report['pass']['fully_right'])}.",
        "",
        "## Per field and method",
        "",
    ]
    for split in SPLITS:
        lines += [f"### {split.capitalize()}", "", "| Field | " + " | ".join(ORDER) + " |",
                  "|---|" + "---|" * len(ORDER)]  # fmt: skip
        for field in FIELDS:
            cells = [_field_rate(methods[m], field, split) for m in ORDER]
            lines.append(f"| {LABELS[field]} | " + " | ".join(cells) + " |")
        lines.append("")
    lines += [
        "## The per-field choice",
        "",
        "Rule, fixed in the plan before any result: per field, the method with the highest "
        "accuracy on all messages; within one point of the best, the cheaper one (rules, then "
        "combined, then model).",
        "",
        "| Field | Chosen |",
        "|---|---|",
    ]
    lines += [f"| {LABELS[f]} | {report['decision'][f]} |" for f in FIELDS]
    lines += ["", "## Floors (seen layouts)", ""]
    lines += _floors({m: methods[m] for m in ORDER})
    lines += ["", "## Calls and cost, from the call log", ""]
    lines += _calls({m: report["calls"][m] for m in ORDER})
    lines += [
        "",
        "Spend on the message stream leaves out the clean-twin calls the injection measure "
        "makes; cost per lead divides it by the leads the run created.",
        "",
        "## Message fully right",
        "",
        "| | " + " | ".join(ORDER) + " |",
        "|---|" + "---|" * len(ORDER),
        "| All messages | " + " | ".join(_rate(report["fully_right"][m]) for m in ORDER) + " |",
        "",
    ]
    return "\n".join(lines)


def confirm_markdown(report: dict[str, Any], date: str) -> str:
    seed = report["seed"]
    shipped = report["shipped"]
    lines = [
        f"# Confirm run, seed {seed}",
        "",
        f"Run date {date}. Model `{report['model']}`, {report['messages']} messages, with the "
        "per-field methods committed in `config/field_methods.yaml` before this seed was "
        "first run. Answers recorded from live calls and replayed from `evals/recordings/`; "
        "CI reruns it in replay mode and compares the bytes.",
        "",
        f"`config/field_methods.yaml` sha256: `{report['field_methods_sha256']}`",
        "",
        "| Field | Method |",
        "|---|---|",
    ]
    lines += [f"| {LABELS[f]} | {report['field_methods'][f]} |" for f in FIELDS]
    lines += ["", "## Floors (seen layouts)", ""]
    lines += _floors({"shipped": shipped})
    lines += ["", "## Fields", "", "| Field | Seen | Unseen | All |", "|---|---|---|---|"]
    for field in FIELDS:
        cells = [_field_rate(shipped, field, s) for s in SPLITS]
        lines.append(f"| {LABELS[field]} | " + " | ".join(cells) + " |")
    lines += ["", "## Calls and cost, from the call log", ""]
    lines += _calls({"shipped": report["calls"]})
    lines += [
        "",
        f"Message fully right: {_rate(report['fully_right'])}.",
        "",
    ]
    return "\n".join(lines)


def _write(report: dict[str, Any], text: str, paths: tuple[Path, Path]) -> tuple[Path, Path]:
    json_path, md_path = paths
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    md_path.write_text(text)
    return json_path, md_path


def write_model(report: dict[str, Any], date: str, root: Path = REPORTS) -> tuple[Path, Path]:
    return _write(report, model_markdown(report, date), model_paths(report["seed"], date, root))


def write_confirm(report: dict[str, Any], date: str, root: Path = REPORTS) -> tuple[Path, Path]:
    return _write(report, confirm_markdown(report, date), confirm_paths(report["seed"], date, root))
