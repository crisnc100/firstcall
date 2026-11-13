"""The note eval's scorer, frozen with the note gold before any model call (ADR-007).

It scores the note reader's answers against the gold labels of the note scripts. A change to
this file after the gold is committed needs an entry in evals/gold/CHANGELOG.md and the
orchestrator's approval, and none is allowed once the held-out seed-11 run has started.

Every rule here is chosen so that a reader cannot pass by filling everything, by copying the
note through, or by inventing a figure:
- A field counts as right only when its value is right. A wrong value counts as one false
  fill and one miss, so "$400k" for "$40k" loses twice.
- A key term counts only in the field it belongs to, and a field that holds most of the
  note's words (a copy) counts as having none.
- A number or a person's name in the answer that the note does not contain makes the note
  "unsupported".
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from functools import cache
from typing import Any

from firstcall.core.note_terms import (
    ACTION_KINDS,
    BANT_FIELDS,
    EMPTY_AUTHORITY,
    NON_ACTION_KINDS,
    TERM_FIELDS,
    TEXT_FIELDS,
)
from firstcall.evaluation.metrics import Rate

# A field holding this share of the note's distinct words or more is a copy of the note.
COPY_SHARE = 0.8
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 0
F1_FLOOR = 0.85
TERM_FLOOR = 0.80
EXACT_FLOOR = 0.85
UNSUPPORTED_MAX = 1

UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
SCALES = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
SUFFIXES = {
    "k": 1_000, "thousand": 1_000, "grand": 1_000,
    "m": 1_000_000, "mil": 1_000_000, "million": 1_000_000,
    "bn": 1_000_000_000, "billion": 1_000_000_000,
}  # fmt: skip
DIGITS = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?"
    r"(?:\s*(k|m|mil|million|thousand|grand|bn|billion)\b)?(?:st|nd|rd|th)?",
    re.IGNORECASE,
)
WORD = re.compile(r"[a-z]+", re.IGNORECASE)

MONTHS = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)  # fmt: skip
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
# Names in Faker's en_US lists that are also everyday English words. A capitalised one in an
# answer is far more likely a word ("Price", "Park") than an invented person.
EVERYDAY_NAMES = (
    "Amber", "Angel", "Archer", "Autumn", "Baker", "Ball", "Barber", "Bass", "Bean", "Beard",
    "Bell", "Berry", "Best", "Bill", "Bird", "Bishop", "Black", "Bond", "Booth", "Branch",
    "Bright", "Brown", "Buck", "Bush", "Butler", "Cannon", "Carpenter", "Case", "Chase", "Cherry",
    "Church", "Clay", "Cook", "Cooper", "Cross", "Crystal", "Daisy", "Dale", "Dawn", "Day",
    "Dean", "Diamond", "Drake", "Duke", "Earl", "Faith", "Farmer", "Fisher", "Ford", "Fox",
    "Frank", "Freeman", "Frost", "Fry", "Gamble", "Gay", "Glass", "Glen", "Golden", "Good",
    "Grace", "Grant", "Gray", "Green", "Gross", "Guy", "Hall", "Hardy", "Hart", "Heath",
    "Heather", "Hill", "Holly", "Hood", "Horn", "House", "Hunt", "Jack", "Jade", "Jay", "Joy",
    "Key", "King", "Knight", "Lamb", "Lance", "Lane", "Little", "Long", "Love", "Mark", "Marsh",
    "May", "Melody", "Moon", "Moss", "Noble", "Page", "Park", "Patty", "Penny", "Pierce", "Pope",
    "Porter", "Potter", "Price", "Prince", "Ray", "Reed", "Rich", "Rose", "Rush", "Sandy",
    "Savage", "Sharp", "Short", "Small", "Snow", "Sparks", "Stark", "Stone", "Stout", "Strong",
    "Summer", "Tanner", "Tucker", "Turner", "Valentine", "Wade", "Walker", "Wall", "Ward", "Ware",
    "West", "White", "Wise", "Wolf", "Wood", "Young",
)  # fmt: skip
COMMON_WORDS = frozenset(EVERYDAY_NAMES)
CAPITALISED = re.compile(r"\b[A-Z][a-z]+\b")


@cache
def person_names() -> frozenset[str]:
    """Faker's en_US first and last names, less months, weekdays and common words."""
    from faker.providers.person.en_US import Provider

    names = set(Provider.first_names) | set(Provider.last_names)
    return frozenset(names - set(MONTHS) - set(WEEKDAYS) - COMMON_WORDS)


def _words_value(words: Sequence[str]) -> int | None:
    total, current, seen = 0, 0, False
    for word in words:
        if word in UNITS:
            current += UNITS[word]
            seen = True
        elif word == "hundred":
            current = (current or 1) * 100
            seen = True
        elif word in SCALES:
            total += (current or 1) * SCALES[word]
            current = 0
            seen = True
    return total + current if seen else None


def _word_numbers(text: str) -> set[Decimal]:
    """Numbers written as words: "forty thousand", "a hundred", "half a million"."""
    found: set[Decimal] = set()
    tokens = [t.lower() for t in WORD.findall(text.replace("-", " "))]
    index = 0
    while index < len(tokens):
        word = tokens[index]
        if word == "half" and tokens[index + 1 : index + 3] in (
            ["a", "million"],
            ["a", "billion"],
        ):
            found.add(Decimal(SCALES[tokens[index + 2]]) / 2)
            index += 3
            continue
        starts = word in UNITS or word == "hundred" or word in SCALES
        a_scale = (
            word == "a"
            and index + 1 < len(tokens)
            and (tokens[index + 1] == "hundred" or tokens[index + 1] in SCALES)
        )
        if not (starts or a_scale):
            index += 1
            continue
        run: list[str] = []
        if a_scale:
            index += 1
        while index < len(tokens):
            word = tokens[index]
            if word in UNITS or word == "hundred" or word in SCALES:
                run.append(word)
            elif (
                # "four hundred and fifty" is one number; "430 thousand and 505 thousand" two.
                word == "and"
                and run[-1:] == ["hundred"]
                and index + 1 < len(tokens)
                and tokens[index + 1] in UNITS
            ):
                pass
            else:
                break
            index += 1
        value = _words_value(run)
        if value is not None:
            found.add(Decimal(value))
    return found


def read_numbers(text: str) -> set[Decimal]:
    """Every number in the text, as a value: "$450k", "450,000" and "four hundred fifty
    thousand" are all 450000; "1.5 million" is 1500000."""
    found: set[Decimal] = set()
    for match in DIGITS.finditer(text):
        whole, fraction, suffix = match.group(1), match.group(2) or "", match.group(3)
        try:
            value = Decimal(whole.replace(",", "") + fraction)
        except InvalidOperation:  # pragma: no cover - the pattern only matches digits
            continue
        if suffix:
            value *= SUFFIXES[suffix.lower()]
        found.add(value.normalize())
    # Words are read only outside the digit numbers, so the "million" of "1.5 million" is
    # not read a second time as one million.
    found |= {v.normalize() for v in _word_numbers(DIGITS.sub(" ", text))}
    return found


def supported_numbers(script: str) -> set[Decimal]:
    """The script's numbers, each also scaled by a thousand and a million. A note that says
    "between 380 and 420 thousand" supports 380000 as well as 380."""
    base = read_numbers(script)
    scaled = {(v * m).normalize() for v in base for m in (1_000, 1_000_000)}
    return base | scaled


def names_in(text: str) -> set[str]:
    names = person_names()
    return {word for word in CAPITALISED.findall(text) if word in names}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.casefold()))


def _norm(text: str) -> str:
    return " ".join(text.casefold().replace("-", " ").split())


def field(answer: Mapping[str, Any] | None, path: str) -> Any:
    """A dotted field of an answer, or None."""
    value: Any = answer
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)  # type: ignore[union-attr]
    return value


def _text(answer: Mapping[str, Any] | None, path: str) -> str:
    value = field(answer, path)
    return value if isinstance(value, str) else ""


def copied(script: str, text: str) -> bool:
    """True when the text holds COPY_SHARE or more of the script's distinct words."""
    script_words = _words(script)
    if not script_words or not text:
        return False
    return len(script_words & _words(text)) / len(script_words) >= COPY_SHARE


def filled(answer: Mapping[str, Any] | None, name: str) -> bool:
    if name == "budget":
        return bool(
            _text(answer, "budget.text").strip()
            or field(answer, "budget.min") is not None
            or field(answer, "budget.max") is not None
        )
    if name == "authority":
        value = field(answer, "authority.value")
        return isinstance(value, str) and value != EMPTY_AUTHORITY
    if name == "need":
        return bool(_text(answer, "need").strip())
    return bool(_text(answer, "timeline.text").strip() or field(answer, "timeline.month"))


def gold_filled(label: Mapping[str, Any], name: str) -> bool:
    if name == "authority":
        return label["authority"] != EMPTY_AUTHORITY
    return label[name] is not None


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value)).normalize()
    except InvalidOperation:
        return None


def right(
    answer: Mapping[str, Any] | None, label: Mapping[str, Any], script: str, name: str
) -> bool:
    """A filled field whose value matches the gold."""
    if not (filled(answer, name) and gold_filled(label, name)):
        return False
    if name == "budget":
        gold = label["budget"]
        return (
            not copied(script, _text(answer, "budget.text"))
            and _number(field(answer, "budget.min")) == _number(gold["min"])
            and _number(field(answer, "budget.max")) == _number(gold["max"])
        )
    if name == "authority":
        return field(answer, "authority.value") == label["authority"]
    if name == "need":
        text = _text(answer, "need")
        return not copied(script, text) and any(
            _norm(term) in _norm(text) for term in label["terms"]["need"]
        )
    text = _text(answer, "timeline.text")
    return (
        not copied(script, text) and field(answer, "timeline.month") == label["timeline"]["month"]
    )


def unsupported(script: str, answer: Mapping[str, Any] | None) -> list[str]:
    """What the answer states that the script does not: numbers and person names."""
    numbers = supported_numbers(script)
    script_words = _words(script)
    found: list[str] = []
    for path in TEXT_FIELDS:
        text = _text(answer, path)
        for value in sorted(read_numbers(text) - numbers):
            found.append(f"{path}: number {value}")
        for name in sorted(names_in(text)):
            if name.casefold() not in script_words:
                found.append(f"{path}: name {name}")
    for path in ("budget.min", "budget.max"):
        value = _number(field(answer, path))
        if value is not None and value not in numbers:
            found.append(f"{path}: number {value}")
    return found


def actions(answer: Mapping[str, Any] | None) -> set[str]:
    listed = field(answer, "actions")
    if not isinstance(listed, list):
        return set()
    kinds: set[str] = set()
    for item in listed:  # type: ignore[union-attr]
        kind = field(item, "kind")  # type: ignore[arg-type]
        if kind in ACTION_KINDS:
            kinds.add(kind)
    return kinds


def _f1(counts: Mapping[str, int]) -> float | None:
    denominator = 2 * counts["tp"] + counts["fp"] + counts["fn"]
    return None if denominator == 0 else 2 * counts["tp"] / denominator


def _field_counts(rows: Iterable[tuple[Any, Mapping[str, Any], str]], name: str) -> dict[str, int]:
    counts = {"tp": 0, "fp": 0, "fn": 0, "filled": 0, "gold_filled": 0, "scripts": 0}
    for answer, label, script in rows:
        counts["scripts"] += 1
        is_filled, is_gold = filled(answer, name), gold_filled(label, name)
        counts["filled"] += is_filled
        counts["gold_filled"] += is_gold
        if right(answer, label, script, name):
            counts["tp"] += 1
            continue
        counts["fp"] += is_filled
        counts["fn"] += is_gold
    return counts


def _bootstrap(rows: list[tuple[Any, Mapping[str, Any], str]], name: str) -> list[float] | None:
    if not rows:
        return None
    per_row = [_field_counts([row], name) for row in rows]
    rng = random.Random(BOOTSTRAP_SEED)
    values: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        total = {"tp": 0, "fp": 0, "fn": 0}
        for _ in rows:
            pick = per_row[rng.randrange(len(rows))]
            for key in total:
                total[key] += pick[key]
        values.append(_f1(total) or 0.0)
    values.sort()
    low = values[int(0.025 * BOOTSTRAP_RESAMPLES)]
    high = values[int(0.975 * BOOTSTRAP_RESAMPLES) - 1]
    return [round(low, 4), round(high, 4)]


def score_part(
    scripts: Mapping[str, str],
    labels: Sequence[Mapping[str, Any]],
    answers: Mapping[str, Mapping[str, Any] | None],
) -> dict[str, Any]:
    """Every note metric over these labels. A missing or unreadable answer is scored as an
    answer that fills nothing."""
    rows = [(answers.get(label["id"]), label, scripts[label["id"]]) for label in labels]
    fields: dict[str, Any] = {}
    for name in BANT_FIELDS:
        counts = _field_counts(rows, name)
        f1 = _f1(counts)
        fields[name] = {
            **{k: counts[k] for k in ("tp", "fp", "fn")},
            "f1": None if f1 is None else round(f1, 4),
            "f1_interval": _bootstrap(rows, name),
            "fill_rate": Rate(counts["filled"], counts["scripts"]).as_json(),
            "gold_fill_rate": Rate(counts["gold_filled"], counts["scripts"]).as_json(),
        }

    authority = [r for r in rows if r[1]["authority"] != EMPTY_AUTHORITY]
    authority_hits = sum(field(a, "authority.value") == lb["authority"] for a, lb, _ in authority)
    months = [r for r in rows if r[1]["timeline"] and r[1]["timeline"]["month"]]
    month_hits = sum(field(a, "timeline.month") == lb["timeline"]["month"] for a, lb, _ in months)

    term_hits = term_total = copies = 0
    for answer, label, script in rows:
        for name, path in TERM_FIELDS.items():
            text = _text(answer, path)
            is_copy = copied(script, text)
            copies += is_copy
            for term in label["terms"][name]:
                term_total += 1
                term_hits += (not is_copy) and _norm(term) in _norm(text)

    unsupported_ids = [lb["id"] for a, lb, s in rows if unsupported(s, a)]

    tp = fp = fn = 0
    non_actions: dict[str, list[int]] = {kind: [0, 0] for kind in NON_ACTION_KINDS}
    for answer, label, _ in rows:
        predicted, gold = actions(answer), set(label["actions"])
        tp += len(predicted & gold)
        fp += len(predicted - gold)
        fn += len(gold - predicted)
        if label["kind"] in non_actions:
            non_actions[label["kind"]][0] += not predicted
            non_actions[label["kind"]][1] += 1

    return {
        "scripts": len(rows),
        "unreadable": sum(a is None for a, _, _ in rows),
        "fields": fields,
        "authority_exact": Rate(authority_hits, len(authority)).as_json(),
        "month_exact": Rate(month_hits, len(months)).as_json(),
        "key_terms": Rate(term_hits, term_total).as_json(),
        "copied_fields": copies,
        "unsupported": {"count": len(unsupported_ids), "ids": unsupported_ids},
        "actions": {
            "precision": Rate(tp, tp + fp).as_json(),
            "recall": Rate(tp, tp + fn).as_json(),
        },
        "non_actions_without_suggestion": {
            kind: Rate(hits, total).as_json() for kind, (hits, total) in non_actions.items()
        },
    }


def floors(part: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The notes floors, each met when its point estimate meets it."""
    rows: list[dict[str, Any]] = []

    def add(measure: str, bound: str, value: float | int | None, met: bool | None) -> None:
        rows.append({"measure": measure, "floor": bound, "value": value, "met": met})

    for name in BANT_FIELDS:
        f1 = part["fields"][name]["f1"]
        add(f"{name} F1", f">= {F1_FLOOR}", f1, None if f1 is None else f1 >= F1_FLOOR)
    for key, measure, bound in (
        ("key_terms", "key-term recall", TERM_FLOOR),
        ("authority_exact", "authority exact", EXACT_FLOOR),
        ("month_exact", "timeline month exact", EXACT_FLOOR),
    ):
        rate = part[key]["rate"]
        add(measure, f">= {bound}", rate, None if rate is None else rate >= bound)
    count = part["unsupported"]["count"]
    add("notes with an unsupported number or name", f"<= {UNSUPPORTED_MAX}", count, count <= 1)
    return rows


def all_met(part: Mapping[str, Any]) -> bool:
    return all(row["met"] for row in floors(part))


def score(
    scripts: Sequence[Mapping[str, Any]],
    labels: Sequence[Mapping[str, Any]],
    answers: Mapping[str, Mapping[str, Any] | None],
) -> dict[str, Any]:
    """All scripts, then the seen and unseen phrasing apart. Floors apply to all scripts."""
    texts = {s["id"]: s["text"] for s in scripts}
    whole = score_part(texts, labels, answers)
    return {
        "all": {**whole, "floors": floors(whole)},
        "seen": score_part(texts, [lb for lb in labels if lb["seen"]], answers),
        "unseen": score_part(texts, [lb for lb in labels if not lb["seen"]], answers),
    }


def _month_after(note_date: str, months: int) -> str:
    year, month = int(note_date[:4]), int(note_date[5:7]) + months
    year += (month - 1) // 12
    return f"{year:04d}-{(month - 1) % 12 + 1:02d}"


def baselines(
    scripts: Sequence[Mapping[str, Any]], labels: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, dict[str, Any] | None]]:
    """Answers no honest reader gives, which must all miss the floors, and the gold itself
    written as an answer, which must meet them."""
    texts = {s["id"]: s["text"] for s in scripts}
    dates = {s["id"]: s["note_date"] for s in scripts}
    always: dict[str, dict[str, Any] | None] = {}
    never: dict[str, dict[str, Any] | None] = {}
    copy: dict[str, dict[str, Any] | None] = {}
    oracle: dict[str, dict[str, Any] | None] = {}
    for label in labels:
        key, text = label["id"], texts[label["id"]]
        always[key] = {
            "clean_note": "",
            "budget": {"text": "budget", "min": 500000, "max": 500000},
            "authority": {"value": "decision_maker", "others": ""},
            "need": "a home",
            "timeline": {"text": "soon", "month": _month_after(dates[key], 3)},
            "notes": "",
            "actions": [{"kind": "call", "quote": ""}],
        }
        never[key] = {
            "clean_note": "",
            "budget": None,
            "authority": None,
            "need": None,
            "timeline": None,
            "notes": None,
            "actions": [],
        }
        copy[key] = {
            "clean_note": text,
            "budget": {"text": text, "min": None, "max": None},
            "authority": {"value": EMPTY_AUTHORITY, "others": text},
            "need": text,
            "timeline": {"text": text, "month": None},
            "notes": text,
            "actions": [],
        }
        terms = label["terms"]
        budget, timeline = label["budget"], label["timeline"]
        oracle[key] = {
            "clean_note": "",
            "budget": None
            if budget is None
            else {"text": " ".join(terms["budget"]), "min": budget["min"], "max": budget["max"]},
            "authority": {"value": label["authority"], "others": " ".join(terms["authority"])},
            "need": None if label["need"] is None else " ".join(terms["need"]),
            "timeline": None
            if timeline is None
            else {"text": " ".join(terms["timeline"]) or "timing", "month": timeline["month"]},
            "notes": None,
            "actions": [{"kind": kind, "quote": ""} for kind in label["actions"]],
        }
    return {"always_fill": always, "never_fill": never, "copy_through": copy, "gold": oracle}
