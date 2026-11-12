"""Escalation: which levels are due for an unclaimed lead.

Clocks run from arrival, so time a lead spent in Unassigned counts once it is placed. Team
levels count the team's business minutes. Unassigned has no team and so no hours; its level
counts wall-clock minutes. Each level fires once per lead, ever: the caller stores fired
levels under a unique key and passes them back in.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from firstcall.core.hours import Hours, business_minutes, wall_minutes

Audience = Literal["team_leads", "admins_and_monitors", "admins"]


@dataclass(frozen=True)
class Level:
    name: str
    minutes: float
    audience: Audience
    in_team: bool


TEAM_LEADS = "team_unclaimed"
ADMINS = "team_still_unclaimed"
UNASSIGNED = "unassigned_still"
DEFAULTS: Mapping[str, float] = {TEAM_LEADS: 15, ADMINS: 60, UNASSIGNED: 15}
AUDIENCES: Mapping[str, Audience] = {
    TEAM_LEADS: "team_leads",
    ADMINS: "admins_and_monitors",
    UNASSIGNED: "admins",
}


def levels(overrides: Mapping[str, float] | None = None) -> tuple[Level, ...]:
    """The levels with a team's own minutes in place of the defaults."""
    minutes = {**DEFAULTS, **(overrides or {})}
    return (
        Level(TEAM_LEADS, minutes[TEAM_LEADS], AUDIENCES[TEAM_LEADS], True),
        Level(ADMINS, minutes[ADMINS], AUDIENCES[ADMINS], True),
        Level(UNASSIGNED, minutes[UNASSIGNED], AUDIENCES[UNASSIGNED], False),
    )


def due(
    state: str,
    in_team: bool,
    arrival: datetime,
    now: datetime,
    hours: Hours | None,
    fired: frozenset[str],
    overrides: Mapping[str, float] | None = None,
) -> list[Level]:
    """Levels whose time has come and that have not fired. Only a pool lead escalates."""
    if state != "pool":
        return []
    if in_team and hours is None:
        raise ValueError("a team lead needs its team's hours")
    found: list[Level] = []
    for level in levels(overrides):
        if level.in_team != in_team or level.name in fired:
            continue
        waited = (
            business_minutes(arrival, now, hours)
            if in_team and hours is not None
            else wall_minutes(arrival, now)
        )
        if waited >= level.minutes:
            found.append(level)
    return found
