"""The cadence: when an owner should next be in touch with a buyer.

A new lead's steps fall on its arrival date plus each day in `steps`, in the team's local
time. After the last step the team's steady rhythm takes over, counted from the later of the
last step and the last touch. A nurture lead runs monthly from the later of entering nurture
and the last touch. A step that lands on a day the team is closed moves to its next open day.

A step is due when its date has come and no touch happened on or after it. The nightly job
opens a follow-up for the current step on its due date, so "done on time" means done by then.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import cast

from firstcall.core.hours import Hours

RHYTHM_DAYS: Mapping[str, int] = {"daily": 1, "weekly": 7, "monthly": 30}
DEFAULT_STEPS: tuple[int, ...] = (0, 1, 3, 7, 14, 30)
DEFAULT_STEADY = "weekly"
NURTURE = "monthly"
# How far ahead a closed day can push a step before the team's config is called broken.
MAX_CLOSED_DAYS = 14


@dataclass(frozen=True)
class Cadence:
    steps: tuple[int, ...] = DEFAULT_STEPS
    steady: str = DEFAULT_STEADY


@dataclass(frozen=True)
class Step:
    due_on: date
    # What the step is, for the follow-up: "day 3", "weekly" or "monthly".
    label: str


def cadence_from(config: Mapping[str, object] | None) -> Cadence:
    """A team's cadence, checked: steps are distinct whole days from 0 up, and the steady
    rhythm is one of daily, weekly or monthly."""
    if not config:
        return Cadence()
    steps_value = config.get("steps", list(DEFAULT_STEPS))
    steady = config.get("steady", DEFAULT_STEADY)
    if not isinstance(steps_value, list | tuple) or not steps_value:
        raise ValueError("cadence steps must be a non-empty list of days")
    steps: list[int] = []
    for item in cast(Sequence[object], steps_value):
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ValueError("each cadence step is a whole number of days, 0 or more")
        steps.append(item)
    if sorted(set(steps)) != steps:
        raise ValueError("cadence steps must be distinct and in order")
    if steady not in RHYTHM_DAYS:
        raise ValueError(f"the steady rhythm must be one of {tuple(RHYTHM_DAYS)}")
    return Cadence(tuple(steps), str(steady))


def open_day(day: date, hours: Hours) -> date:
    """The day itself if the team is open then, else its next open day."""
    for offset in range(MAX_CLOSED_DAYS + 1):
        candidate = day + timedelta(days=offset)
        if candidate.weekday() in hours.days:
            return candidate
    raise ValueError("the team has no open day within two weeks")


def current_step(
    state: str,
    arrival: date,
    today: date,
    last_touch: date | None,
    nurture_since: date | None,
    cadence: Cadence,
    hours: Hours,
) -> Step | None:
    """The latest step whose due date has come, or None. Dates are the team's local dates."""
    candidates: list[Step] = []
    if state == "owned":
        for day in cadence.steps:
            candidates.append(Step(open_day(arrival + timedelta(days=day), hours), f"day {day}"))
        base = arrival + timedelta(days=cadence.steps[-1])
        rhythm = cadence.steady
    elif state == "nurture":
        base = nurture_since or arrival
        rhythm = NURTURE
    else:
        return None
    if last_touch is not None and last_touch > base:
        base = last_touch
    period = timedelta(days=RHYTHM_DAYS[rhythm])
    # The steady steps after the base, up to today: the latest one whose open day has come is
    # the only one that matters. A closed day can push the newest past today, so earlier
    # steps are tried; open_day moves a date at most two weeks, so few are.
    if today > base:
        for count in range((today - base) // period, 0, -1):
            due_on = open_day(base + period * count, hours)
            if due_on <= today:
                candidates.append(Step(due_on, rhythm))
                break
    # Candidates are in cadence order, so when a closed day pushes two steps onto one date
    # the later step wins the tie.
    found: Step | None = None
    for step in candidates:
        if step.due_on <= today and (found is None or step.due_on >= found.due_on):
            found = step
    return found


def is_due(step: Step | None, last_touch: date | None) -> bool:
    """A step is due when no touch happened on or after its date."""
    return step is not None and (last_touch is None or last_touch < step.due_on)


GAPS: tuple[tuple[str, str], ...] = (
    ("budget", "Budget not known yet."),
    ("authority", "Decision maker not known yet."),
    ("need", "Needs not known yet."),
    ("timeline", "Timeline not known yet."),
)


def next_step(today: date, arrival: date, last_touch: date | None, known: frozenset[str]) -> str:
    """The follow-up's rules-based line: days since the last touch, and the first profile
    field nobody has filled. No model call."""
    if last_touch is None:
        days = (today - arrival).days
        line = f"No contact yet, {days} day{'s' if days != 1 else ''} since arrival."
    else:
        days = (today - last_touch).days
        line = f"No contact in {days} day{'s' if days != 1 else ''}."
    for name, gap in GAPS:
        if name not in known:
            return f"{line} {gap}"
    return line
