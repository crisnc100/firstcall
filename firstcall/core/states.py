"""The lead state machine: every allowed transition, who may make it and the event it writes.

Anything not in TRANSITIONS is refused. "Unassigned" is a pool with no team, so the rows that
care where a pool lead sits say so in `where`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

State = Literal["none", "pool", "owned", "nurture", "won", "lost", "not_real"]
Relation = Literal["system", "owner", "team_member", "team_lead", "monitor", "admin"]
Where = Literal["any", "team", "unassigned"]

STATES: tuple[State, ...] = ("none", "pool", "owned", "nurture", "won", "lost", "not_real")
OPEN: frozenset[State] = frozenset({"pool", "owned", "nurture"})
OWNED: frozenset[State] = frozenset({"owned", "nurture"})
RELATIONS: tuple[Relation, ...] = (
    "system",
    "owner",
    "team_member",
    "team_lead",
    "monitor",
    "admin",
)


@dataclass(frozen=True)
class Transition:
    action: str
    sources: frozenset[State]
    targets: frozenset[State]
    who: frozenset[Relation]
    event: str
    where: Where = "any"


def _t(
    action: str,
    sources: set[State],
    targets: set[State],
    who: set[Relation],
    event: str,
    where: Where = "any",
) -> Transition:
    return Transition(action, frozenset(sources), frozenset(targets), frozenset(who), event, where)


TRANSITIONS: tuple[Transition, ...] = (
    _t("create", {"none"}, {"pool"}, {"system"}, "lead.created"),
    _t("place", {"pool"}, {"pool"}, {"monitor", "admin"}, "lead.placed", "unassigned"),
    _t("claim", {"pool"}, {"owned"}, {"team_member", "team_lead"}, "lead.claimed", "team"),
    _t("release", {"owned"}, {"pool"}, {"owner"}, "lead.released"),
    _t("return", {"owned"}, {"pool"}, {"team_lead", "admin"}, "lead.returned"),
    _t("owner_left", {"owned", "nurture"}, {"pool"}, {"system"}, "lead.returned"),
    _t(
        "reassign",
        {"owned", "nurture"},
        {"owned", "nurture"},
        {"team_lead", "admin"},
        "lead.reassigned",
    ),
    _t("move_team", set(OPEN), {"pool"}, {"team_lead", "admin"}, "lead.moved_team", "team"),
    _t("set_nurture", {"owned"}, {"nurture"}, {"owner", "team_lead"}, "lead.outcome"),
    _t("resume", {"nurture"}, {"owned"}, {"owner", "team_lead"}, "lead.reopened"),
    _t("buyer_wrote", {"nurture"}, {"owned"}, {"system"}, "lead.activity"),
    _t(
        "set_outcome",
        {"owned", "nurture"},
        {"won", "lost", "not_real"},
        {"owner", "team_lead"},
        "lead.outcome",
    ),
    _t("buyer_reopened", {"lost", "not_real"}, {"owned", "pool"}, {"system"}, "lead.reopened"),
    _t(
        "reopen",
        {"won", "lost", "not_real"},
        {"owned", "pool"},
        {"team_lead", "admin"},
        "lead.reopened",
    ),
    _t("buyer_wrote", {"won"}, {"won"}, {"system"}, "lead.activity"),
)


class RefusedError(Exception):
    """A transition the table does not allow, or one this actor may not make."""


def check(
    action: str,
    source: State,
    target: State,
    relations: frozenset[Relation],
    unassigned: bool = False,
) -> str:
    """The event type the transition writes. Raises RefusedError for anything not in the
    table. A reassign keeps the lead's state, so one that would change it is refused."""
    for row in TRANSITIONS:
        if row.action != action or source not in row.sources or target not in row.targets:
            continue
        if (row.where == "team" and unassigned) or (row.where == "unassigned" and not unassigned):
            continue
        if action == "reassign" and source != target:
            continue
        if row.who & relations:
            return row.event
    raise RefusedError(f"{action} from {source} to {target} is not allowed for this actor")
