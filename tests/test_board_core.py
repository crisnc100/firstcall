"""The pure board rules: business hours, the state machine table, access and escalation."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from firstcall.core import escalation
from firstcall.core.access import LeadRef, Viewer, can_see, can_see_team, relations
from firstcall.core.hours import (
    after_business_minutes,
    business_minutes,
    hours_from,
    wall_minutes,
)
from firstcall.core.states import RELATIONS, STATES, RefusedError, check

DEFAULT_HOURS = {
    "days": ["mon", "tue", "wed", "thu", "fri", "sat"],
    "open": "09:00",
    "close": "18:00",
}
NEW_YORK = hours_from(DEFAULT_HOURS, "America/New_York")


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[misc]


# --- business hours (F23) ---------------------------------------------------------------


def test_inside_hours_business_and_wall_minutes_agree() -> None:
    # Monday 2 March 2026, 10:00 to 10:30 New York time (EST, UTC-5).
    start, end = utc(2026, 3, 2, 15, 0), utc(2026, 3, 2, 15, 30)
    assert business_minutes(start, end, NEW_YORK) == 30
    assert wall_minutes(start, end) == 30


def test_an_after_hours_arrival_starts_counting_at_the_next_opening() -> None:
    # Monday 20:00 to Tuesday 09:10 local.
    start, end = utc(2026, 3, 3, 1, 0), utc(2026, 3, 3, 14, 10)
    assert business_minutes(start, end, NEW_YORK) == 10
    assert wall_minutes(start, end) == 790
    assert after_business_minutes(start, 0, NEW_YORK) == utc(2026, 3, 3, 14, 0)


def test_a_closed_sunday_counts_nothing() -> None:
    # Saturday 17:50 to Monday 09:05 local: 10 minutes Saturday, none Sunday, 5 Monday.
    start, end = utc(2026, 2, 28, 22, 50), utc(2026, 3, 2, 14, 5)
    assert business_minutes(start, end, NEW_YORK) == 15


def test_the_daylight_saving_change_moves_the_opening_in_utc() -> None:
    # Clocks go forward on Sunday 8 March 2026. Saturday 17:00 EST to Monday 10:00 EDT.
    start, end = utc(2026, 3, 7, 22, 0), utc(2026, 3, 9, 14, 0)
    assert business_minutes(start, end, NEW_YORK) == 120
    assert wall_minutes(start, end) == 40 * 60
    assert after_business_minutes(utc(2026, 3, 7, 23, 0), 0, NEW_YORK) == utc(2026, 3, 9, 13, 0)
    assert after_business_minutes(start, 61, NEW_YORK) == utc(2026, 3, 9, 13, 1)


def test_an_end_before_the_start_counts_nothing() -> None:
    assert business_minutes(utc(2026, 3, 2, 16), utc(2026, 3, 2, 15), NEW_YORK) == 0
    assert wall_minutes(utc(2026, 3, 2, 16), utc(2026, 3, 2, 15)) == 0


def test_bad_hours_are_refused() -> None:
    with pytest.raises(ValueError, match="at least one day"):
        hours_from({"days": [], "open": "09:00", "close": "18:00"}, "UTC")
    with pytest.raises(ValueError, match="close after"):
        hours_from({"days": ["mon"], "open": "18:00", "close": "09:00"}, "UTC")


def test_after_business_minutes_gives_up_rather_than_loop_forever() -> None:
    closed_hours = hours_from(DEFAULT_HOURS, "UTC")
    never = replace(closed_hours, days=frozenset())
    with pytest.raises(ValueError, match="no open time"):
        after_business_minutes(utc(2026, 3, 2), 1, never)


# --- the state machine (F22) ------------------------------------------------------------

# The plan's transition table, typed again here rather than read from the code, so a wrong
# row in the code cannot also be the expected value.
# (action, from, to, who, where, event)
PLAN_TABLE = [
    ("create", "none", "pool", "system", "any", "lead.created"),
    ("place", "pool", "pool", "monitor", "unassigned", "lead.placed"),
    ("place", "pool", "pool", "admin", "unassigned", "lead.placed"),
    ("claim", "pool", "owned", "team_member", "team", "lead.claimed"),
    ("claim", "pool", "owned", "team_lead", "team", "lead.claimed"),
    ("release", "owned", "pool", "owner", "any", "lead.released"),
    ("return", "owned", "pool", "team_lead", "any", "lead.returned"),
    ("return", "owned", "pool", "admin", "any", "lead.returned"),
    ("owner_left", "owned", "pool", "system", "any", "lead.returned"),
    ("owner_left", "nurture", "pool", "system", "any", "lead.returned"),
    ("reassign", "owned", "owned", "team_lead", "any", "lead.reassigned"),
    ("reassign", "owned", "owned", "admin", "any", "lead.reassigned"),
    ("reassign", "nurture", "nurture", "team_lead", "any", "lead.reassigned"),
    ("reassign", "nurture", "nurture", "admin", "any", "lead.reassigned"),
    *[
        ("move_team", s, "pool", w, "team", "lead.moved_team")
        for s in ("pool", "owned", "nurture")
        for w in ("team_lead", "admin")
    ],
    ("set_nurture", "owned", "nurture", "owner", "any", "lead.outcome"),
    ("set_nurture", "owned", "nurture", "team_lead", "any", "lead.outcome"),
    ("resume", "nurture", "owned", "owner", "any", "lead.reopened"),
    ("resume", "nurture", "owned", "team_lead", "any", "lead.reopened"),
    ("buyer_wrote", "nurture", "owned", "system", "any", "lead.activity"),
    *[
        ("set_outcome", s, t, w, "any", "lead.outcome")
        for s in ("owned", "nurture")
        for t in ("won", "lost", "not_real")
        for w in ("owner", "team_lead")
    ],
    *[
        ("buyer_reopened", s, t, "system", "any", "lead.reopened")
        for s in ("lost", "not_real")
        for t in ("owned", "pool")
    ],
    *[
        ("reopen", s, t, w, "any", "lead.reopened")
        for s in ("won", "lost", "not_real")
        for t in ("owned", "pool")
        for w in ("team_lead", "admin")
    ],
    ("buyer_wrote", "won", "won", "system", "any", "lead.activity"),
]
ACTIONS = sorted({row[0] for row in PLAN_TABLE})


def _allowed(action: str, source: str, target: str, who: str, unassigned: bool) -> str | None:
    for a, s, t, w, where, event in PLAN_TABLE:
        if (a, s, t, w) != (action, source, target, who):
            continue
        if (where == "team" and unassigned) or (where == "unassigned" and not unassigned):
            continue
        return event
    return None


def test_every_row_passes_and_every_other_combination_is_refused() -> None:
    passed = refused = 0
    for action, source, target, who, unassigned in itertools.product(
        ACTIONS, STATES, STATES, RELATIONS, (False, True)
    ):
        expected = _allowed(action, source, target, who, unassigned)
        if expected is None:
            with pytest.raises(RefusedError):
                check(action, source, target, frozenset({who}), unassigned)  # type: ignore[arg-type]
            refused += 1
        else:
            assert check(action, source, target, frozenset({who}), unassigned) == expected  # type: ignore[arg-type]
            passed += 1
    assert passed == sum(2 if r[4] == "any" else 1 for r in PLAN_TABLE)
    assert refused > 10 * passed


def test_an_unknown_action_and_an_empty_actor_are_refused() -> None:
    with pytest.raises(RefusedError):
        check("teleport", "pool", "owned", frozenset({"admin"}))
    with pytest.raises(RefusedError):
        check("claim", "pool", "owned", frozenset())


# --- access -----------------------------------------------------------------------------

A, B = "team-a", "team-b"
SELLER = Viewer("u1", teams={A: "salesperson"})
LEAD = Viewer("u2", teams={A: "team_lead"})
MONITOR = Viewer("u3", org_roles=frozenset({"monitor"}))
ADMIN = Viewer("u4", org_roles=frozenset({"admin"}))


@pytest.mark.parametrize(
    ("viewer", "lead", "visible"),
    [
        (SELLER, LeadRef(A, None, "pool"), True),
        (SELLER, LeadRef(A, "u1", "owned"), True),
        (SELLER, LeadRef(A, "u9", "owned"), False),
        (SELLER, LeadRef(B, None, "pool"), False),
        (SELLER, LeadRef(None, None, "pool"), False),
        (SELLER, LeadRef(B, "u1", "won"), True),
        (LEAD, LeadRef(A, "u9", "owned"), True),
        (LEAD, LeadRef(B, None, "pool"), False),
        (MONITOR, LeadRef(None, None, "pool"), True),
        (MONITOR, LeadRef(B, "u9", "owned"), True),
        (ADMIN, LeadRef(B, "u9", "owned"), True),
    ],
)
def test_who_can_see_a_lead(viewer: Viewer, lead: LeadRef, visible: bool) -> None:
    assert can_see(viewer, lead) is visible


def test_relations_name_every_role_and_are_empty_for_an_unseen_lead() -> None:
    assert relations(SELLER, LeadRef(A, "u1", "owned")) == {"owner", "team_member"}
    assert relations(LEAD, LeadRef(A, None, "pool")) == {"team_member", "team_lead"}
    assert relations(MONITOR, LeadRef(None, None, "pool")) == {"monitor"}
    assert relations(ADMIN, LeadRef(A, None, "pool")) == {"admin"}
    assert relations(SELLER, LeadRef(B, None, "pool")) == frozenset()


def test_who_can_list_a_pool() -> None:
    assert can_see_team(SELLER, A)
    assert not can_see_team(SELLER, B)
    assert not can_see_team(SELLER, None)
    assert can_see_team(MONITOR, None)
    assert can_see_team(ADMIN, B)


# --- escalation (F24, the pure part) ----------------------------------------------------


def test_team_levels_fire_on_business_minutes_and_not_before() -> None:
    arrival = utc(2026, 3, 2, 15, 0)  # Monday 10:00 local
    at = lambda minutes: arrival + timedelta(minutes=minutes)  # noqa: E731
    names = lambda found: [lvl.name for lvl in found]  # noqa: E731
    assert names(escalation.due("pool", True, arrival, at(14.9), NEW_YORK, frozenset())) == []
    assert names(escalation.due("pool", True, arrival, at(15), NEW_YORK, frozenset())) == [
        escalation.TEAM_LEADS
    ]
    fired = frozenset({escalation.TEAM_LEADS})
    assert names(escalation.due("pool", True, arrival, at(59), NEW_YORK, fired)) == []
    assert names(escalation.due("pool", True, arrival, at(60), NEW_YORK, fired)) == [
        escalation.ADMINS
    ]
    assert escalation.due("owned", True, arrival, at(600), NEW_YORK, frozenset()) == []


def test_a_night_does_not_count_toward_a_team_level() -> None:
    arrival = utc(2026, 3, 2, 22, 50)  # Monday 17:50 local: 10 minutes before close
    tuesday = utc(2026, 3, 3, 14, 4)  # 09:04: 14 business minutes
    assert escalation.due("pool", True, arrival, tuesday, NEW_YORK, frozenset()) == []
    later = tuesday + timedelta(minutes=1)
    found = escalation.due("pool", True, arrival, later, NEW_YORK, frozenset())
    assert [lvl.name for lvl in found] == [escalation.TEAM_LEADS]


def test_unassigned_counts_wall_clock_and_teams_can_set_their_minutes() -> None:
    arrival = utc(2026, 3, 3, 3, 0)  # 22:00 local, closed
    later = arrival + timedelta(minutes=15)
    found = escalation.due("pool", False, arrival, later, None, frozenset())
    assert [(lvl.name, lvl.audience) for lvl in found] == [(escalation.UNASSIGNED, "admins")]
    custom = escalation.due(
        "pool",
        True,
        utc(2026, 3, 2, 15),
        utc(2026, 3, 2, 15, 5),
        NEW_YORK,
        frozenset(),
        {escalation.TEAM_LEADS: 5},
    )
    assert [lvl.name for lvl in custom] == [escalation.TEAM_LEADS]
    with pytest.raises(ValueError, match="hours"):
        escalation.due("pool", True, arrival, later, None, frozenset())
