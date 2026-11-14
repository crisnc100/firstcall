"""The cadence rules (F33), without a database: steps, rhythms, closed days and the next-step
line."""

from __future__ import annotations

from datetime import date, time
from zoneinfo import ZoneInfo

import pytest

from firstcall.core.cadence import (
    Cadence,
    Step,
    cadence_from,
    current_step,
    is_due,
    next_step,
    open_day,
)
from firstcall.core.hours import Hours, hours_from

MON_TO_SAT = hours_from(
    {"days": ["mon", "tue", "wed", "thu", "fri", "sat"], "open": "09:00", "close": "18:00"},
    "America/New_York",
)
MONDAY = date(2026, 3, 2)


def _step(today: date, last_touch: date | None = None, **kw: object) -> Step | None:
    plan = kw.pop("plan", Cadence())
    state = str(kw.pop("state", "owned"))
    arrival = kw.pop("arrival", MONDAY)
    nurture_since = kw.pop("nurture_since", None)
    assert isinstance(plan, Cadence)
    assert isinstance(arrival, date)
    assert nurture_since is None or isinstance(nurture_since, date)
    return current_step(state, arrival, today, last_touch, nurture_since, plan, MON_TO_SAT)


@pytest.mark.parametrize(
    ("today", "due_on", "label"),
    [
        (date(2026, 3, 2), date(2026, 3, 2), "day 0"),
        (date(2026, 3, 3), date(2026, 3, 3), "day 1"),
        (date(2026, 3, 4), date(2026, 3, 3), "day 1"),
        (date(2026, 3, 5), date(2026, 3, 5), "day 3"),
        (date(2026, 3, 9), date(2026, 3, 9), "day 7"),
        (date(2026, 3, 16), date(2026, 3, 16), "day 14"),
        (date(2026, 4, 1), date(2026, 4, 1), "day 30"),
        (date(2026, 4, 7), date(2026, 4, 1), "day 30"),
        (date(2026, 4, 8), date(2026, 4, 8), "weekly"),
        (date(2026, 4, 20), date(2026, 4, 15), "weekly"),
    ],
)
def test_the_default_new_lead_cadence(today: date, due_on: date, label: str) -> None:
    assert _step(today) == Step(due_on, label)


def test_a_step_on_a_closed_day_moves_to_the_next_open_day() -> None:
    saturday = date(2026, 3, 7)
    # Day 1 is Sunday, when the team is closed.
    assert _step(date(2026, 3, 8), arrival=saturday) == Step(saturday, "day 0")
    assert _step(date(2026, 3, 9), arrival=saturday) == Step(date(2026, 3, 9), "day 1")


@pytest.mark.parametrize(("rhythm", "days"), [("daily", 1), ("weekly", 7), ("monthly", 30)])
def test_each_steady_rhythm_counts_from_the_last_step(rhythm: str, days: int) -> None:
    plan = cadence_from({"steps": [0, 1], "steady": rhythm})
    first_steady = date(2026, 3, 3).toordinal() + days
    found = _step(date.fromordinal(first_steady), plan=plan)
    assert found is not None
    assert found.label == rhythm
    assert found.due_on == open_day(date.fromordinal(first_steady), MON_TO_SAT)


def test_a_touch_after_the_last_step_moves_the_steady_rhythm() -> None:
    touch = date(2026, 4, 3)
    assert _step(date(2026, 4, 9), touch) == Step(date(2026, 4, 1), "day 30")
    assert not is_due(_step(date(2026, 4, 9), touch), touch)
    assert _step(date(2026, 4, 10), touch) == Step(date(2026, 4, 10), "weekly")


def test_nurture_runs_monthly_from_entering_nurture_or_the_last_touch() -> None:
    since = date(2026, 3, 10)
    assert _step(date(2026, 4, 8), state="nurture", nurture_since=since) is None
    assert _step(date(2026, 4, 9), state="nurture", nurture_since=since) == Step(
        date(2026, 4, 9), "monthly"
    )
    touched = date(2026, 3, 20)
    assert _step(date(2026, 4, 9), touched, state="nurture", nurture_since=since) is None
    assert _step(date(2026, 4, 20), touched, state="nurture", nurture_since=since) == Step(
        date(2026, 4, 20), "monthly"
    )


def test_a_pool_or_closed_lead_has_no_step() -> None:
    for state in ("pool", "won", "lost", "not_real"):
        assert _step(date(2026, 4, 1), state=state) is None


def test_a_touch_on_or_after_the_step_date_meets_it() -> None:
    step = Step(date(2026, 3, 5), "day 3")
    assert is_due(step, None)
    assert is_due(step, date(2026, 3, 4))
    assert not is_due(step, date(2026, 3, 5))
    assert not is_due(None, None)


@pytest.mark.parametrize(
    "config",
    [
        {"steps": []},
        {"steps": [0, -1]},
        {"steps": [3, 1]},
        {"steps": [1, 1]},
        {"steps": [0, True]},
        {"steps": "0,1"},
        {"steady": "hourly"},
    ],
)
def test_a_bad_cadence_is_refused(config: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"cadence|rhythm"):
        cadence_from(config)


def test_no_config_is_the_default() -> None:
    assert cadence_from({}) == Cadence()
    assert cadence_from(None) == Cadence((0, 1, 3, 7, 14, 30), "weekly")


def test_a_team_with_no_open_day_is_refused() -> None:
    closed = Hours(ZoneInfo("UTC"), frozenset(), time(9), time(17))
    with pytest.raises(ValueError, match="no open day"):
        open_day(MONDAY, closed)


def test_the_next_step_line() -> None:
    assert next_step(date(2026, 3, 8), MONDAY, date(2026, 3, 2), frozenset()) == (
        "No contact in 6 days. Budget not known yet."
    )
    assert next_step(date(2026, 3, 3), MONDAY, None, frozenset({"budget"})) == (
        "No contact yet, 1 day since arrival. Decision maker not known yet."
    )
    everything = frozenset({"budget", "authority", "need", "timeline"})
    assert next_step(date(2026, 3, 3), MONDAY, date(2026, 3, 2), everything) == (
        "No contact in 1 day."
    )
