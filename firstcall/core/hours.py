"""Business hours and speed to lead: minutes that count only while a team is open.

A team's hours are local wall-clock times in its IANA zone, so a daylight-saving change moves
the opening in UTC and never the opening on the team's clock.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# Far enough for any timer a scheduler would ask about; stops a search over a team with no
# open day from running forever.
MAX_DAYS = 3660


@dataclass(frozen=True)
class Hours:
    zone: ZoneInfo
    days: frozenset[int]
    opens: time
    closes: time

    def window(self, day: date) -> tuple[datetime, datetime] | None:
        """The open window on a local date, in UTC, or None on a closed day."""
        if day.weekday() not in self.days:
            return None
        start = datetime.combine(day, self.opens, self.zone)
        end = datetime.combine(day, self.closes, self.zone)
        return start, end


def hours_from(config: Mapping[str, Any], timezone: str) -> Hours:
    """A team's hours from its config: {"days": ["mon", ...], "open": "09:00", "close": "18:00"}."""
    days = frozenset(WEEKDAYS.index(str(d)) for d in config["days"])
    opens = time.fromisoformat(str(config["open"]))
    closes = time.fromisoformat(str(config["close"]))
    if not days or closes <= opens:
        raise ValueError("business hours need at least one day and a close after the open")
    return Hours(ZoneInfo(timezone), days, opens, closes)


def wall_minutes(start: datetime, end: datetime) -> float:
    return max((end - start).total_seconds(), 0.0) / 60


def business_minutes(start: datetime, end: datetime, hours: Hours) -> float:
    """Minutes between start and end that fall inside the team's open hours."""
    if end <= start:
        return 0.0
    seconds = 0.0
    day = start.astimezone(hours.zone).date()
    last = end.astimezone(hours.zone).date()
    while day <= last:
        window = hours.window(day)
        if window is not None:
            lo, hi = max(window[0], start), min(window[1], end)
            if hi > lo:
                seconds += (hi - lo).total_seconds()
        day += timedelta(days=1)
    return seconds / 60


def after_business_minutes(start: datetime, minutes: float, hours: Hours) -> datetime:
    """The moment `minutes` business minutes have passed since start."""
    left = timedelta(minutes=minutes)
    day = start.astimezone(hours.zone).date()
    for _ in range(MAX_DAYS):
        window = hours.window(day)
        if window is not None:
            lo = max(window[0], start)
            if window[1] > lo:
                if lo + left <= window[1]:
                    return lo + left
                left -= window[1] - lo
        day += timedelta(days=1)
    raise ValueError("no open time within ten years")
