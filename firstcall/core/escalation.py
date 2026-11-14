"""Escalation: which levels are due for an unclaimed lead.

Clocks run from the start of the lead's current stay in its pool: arrival for a new lead,
or the moment it was released, returned, moved or placed. Team levels count the team's
business minutes. Unassigned has no team and so no hours; its level counts wall-clock
minutes. Each level fires once per stay: the caller stores fired levels under a unique key
and passes the current stay's back in. Time to claim still counts from arrival.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from firstcall.core.hours import Hours, business_minutes, wall_minutes

Audience = Literal["team_leads", "admins_and_monitors", "admins", "owner"]


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


# An owned lead with no real contact: its owner is told first, then the team leads. These
# count business minutes from the start of the lead's uncontacted stretch.
CONTACT_OWNER = "contact_owner"
CONTACT_TEAM_LEADS = "contact_team_leads"
CONTACT_DEFAULTS: Mapping[str, float] = {CONTACT_OWNER: 240, CONTACT_TEAM_LEADS: 300}


def overrides_from(config: Mapping[str, object]) -> dict[str, float]:
    """A team's escalation minutes, checked: known level names and positive numbers only."""
    found: dict[str, float] = {}
    for name, value in config.items():
        if name not in DEFAULTS and name not in CONTACT_DEFAULTS:
            raise ValueError(f"unknown escalation level {name}")
        if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
            raise ValueError(f"escalation minutes for {name} must be a positive number")
        found[name] = float(value)
    return found


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
    since: datetime,
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
            business_minutes(since, now, hours)
            if in_team and hours is not None
            else wall_minutes(since, now)
        )
        if waited >= level.minutes:
            found.append(level)
    return found


def contact_levels(overrides: Mapping[str, float] | None = None) -> tuple[Level, ...]:
    minutes = {**CONTACT_DEFAULTS, **(overrides or {})}
    return (
        Level(CONTACT_OWNER, minutes[CONTACT_OWNER], "owner", True),
        Level(CONTACT_TEAM_LEADS, minutes[CONTACT_TEAM_LEADS], "team_leads", True),
    )


def contact_due(
    state: str,
    since: datetime | None,
    now: datetime,
    hours: Hours,
    fired: frozenset[str],
    overrides: Mapping[str, float] | None = None,
) -> list[Level]:
    """Contact levels whose time has come in this uncontacted stretch. Only an owned lead
    is watched: nurture is a slow rhythm by choice, and a contact ends the stretch."""
    if state != "owned" or since is None:
        return []
    waited = business_minutes(since, now, hours)
    return [
        level
        for level in contact_levels(overrides)
        if level.name not in fired and waited >= level.minutes
    ]
