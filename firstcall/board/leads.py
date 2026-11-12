"""Lead actions on the board: claim, release, return, reassign, move team, place, owner left.

Each action locks the lead row, asks the state machine, changes the lead, writes one lead
event and queues its alerts, all in the caller's transaction. A viewer who cannot see the
lead, or whose role has no row for the action, gets NotFoundError so the answer is the
same as for a lead that does not exist. A permitted action on a lead in the wrong state
gets ConflictError: that is how the second of two claims learns it lost.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from firstcall import events
from firstcall.board import alerts
from firstcall.core import states
from firstcall.core.access import LeadRef, Viewer, can_see, relations
from firstcall.core.clock import Clock
from firstcall.core.hours import Hours, business_minutes, hours_from, wall_minutes
from firstcall.db import Lead, Membership, Team, User


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


class InvalidError(Exception):
    pass


def ref(lead: Lead) -> LeadRef:
    return LeadRef(
        team_id=str(lead.team_id) if lead.team_id else None,
        owner_id=str(lead.owner_id) if lead.owner_id else None,
        state=lead.state,
    )


def team_hours(team: Team) -> Hours:
    return hours_from(team.business_hours, team.timezone)


def _who(action: str) -> frozenset[states.Relation]:
    found: set[states.Relation] = set()
    for row in states.TRANSITIONS:
        if row.action == action:
            found |= row.who
    return frozenset(found)


def _locked(session: Session, viewer: Viewer, lead_id: uuid.UUID, action: str) -> Lead:
    lead = session.get(Lead, lead_id, with_for_update=True)
    if lead is None or str(lead.org_id) != viewer.org_id:
        raise NotFoundError
    if not _who(action) & _relations(viewer, lead, action):
        raise NotFoundError
    return lead


def _relations(viewer: Viewer, lead: Lead, action: str) -> frozenset[states.Relation]:
    """How the viewer relates to the lead for this action. A teammate who loses a claim race
    can no longer see the lead, yet must hear "already claimed" (409), not 404: they saw it
    in their own pool a moment ago, so that answer leaks nothing new."""
    found = relations(viewer, ref(lead))
    if action == "claim" and viewer.in_team(str(lead.team_id) if lead.team_id else None):
        teammate: frozenset[states.Relation] = frozenset({"team_member"})
        return found | teammate
    return found


def _check(lead: Lead, action: str, target: str, rels: frozenset[states.Relation]) -> str:
    try:
        return states.check(
            action,
            lead.state,
            target,
            rels,
            unassigned=lead.team_id is None,
        )
    except states.RefusedError as refused:
        raise ConflictError(str(refused)) from refused


def _cause(action: str) -> str:
    return f"{action}:{uuid.uuid4()}"


@dataclass(frozen=True)
class Done:
    lead_id: uuid.UUID
    event: str


def claim(session: Session, viewer: Viewer, lead_id: uuid.UUID, clock: Clock) -> Done:
    lead = _locked(session, viewer, lead_id, "claim")
    event = _check(lead, "claim", "owned", _relations(viewer, lead, "claim"))
    now = clock.now()
    user_id = uuid.UUID(viewer.user_id)
    lead.state, lead.owner_id, lead.pool_since = "owned", user_id, None
    if lead.claimed_at is None:
        assert lead.team_id is not None
        team = session.get_one(Team, lead.team_id)
        lead.claimed_at = now
        lead.claim_minutes = _minutes(wall_minutes(lead.arrival_at, now))
        lead.claim_business_minutes = _minutes(
            business_minutes(lead.arrival_at, now, team_hours(team))
        )
    events.write(session, lead, event, _cause("claim"), {"owner_id": viewer.user_id}, now)
    return Done(lead.id, event)


def _minutes(value: float) -> Decimal:
    return Decimal(str(round(value, 2)))


def _to_pool(
    session: Session,
    lead: Lead,
    event: str,
    action: str,
    payload: dict[str, Any],
    clock: Clock,
    team_id: uuid.UUID | None = None,
) -> Done:
    now = clock.now()
    if team_id is not None:
        lead.team_id = team_id
    lead.state, lead.owner_id, lead.pool_since = "pool", None, now
    cause = _cause(action)
    events.write(session, lead, event, cause, payload, now)
    alerts.entered_pool(session, lead, action, cause, now)
    return Done(lead.id, event)


def release(session: Session, viewer: Viewer, lead_id: uuid.UUID, clock: Clock) -> Done:
    lead = _locked(session, viewer, lead_id, "release")
    event = _check(lead, "release", "pool", relations(viewer, ref(lead)))
    payload = {"by": viewer.user_id, "owner_id": str(lead.owner_id)}
    return _to_pool(session, lead, event, "release", payload, clock)


def return_to_pool(session: Session, viewer: Viewer, lead_id: uuid.UUID, clock: Clock) -> Done:
    lead = _locked(session, viewer, lead_id, "return")
    event = _check(lead, "return", "pool", relations(viewer, ref(lead)))
    payload = {"by": viewer.user_id, "owner_id": str(lead.owner_id)}
    return _to_pool(session, lead, event, "return", payload, clock)


def _team_in_org(session: Session, lead: Lead, team_id: uuid.UUID) -> Team:
    team = session.get(Team, team_id)
    if team is None or team.org_id != lead.org_id:
        raise InvalidError("no such team")
    return team


def move_team(
    session: Session, viewer: Viewer, lead_id: uuid.UUID, team_id: uuid.UUID, clock: Clock
) -> Done:
    lead = _locked(session, viewer, lead_id, "move_team")
    event = _check(lead, "move_team", "pool", relations(viewer, ref(lead)))
    team = _team_in_org(session, lead, team_id)
    if team.id == lead.team_id:
        raise InvalidError("the lead is already in that team")
    payload = {"by": viewer.user_id, "from_team": str(lead.team_id), "to_team": str(team.id)}
    return _to_pool(session, lead, event, "move_team", payload, clock, team.id)


def place(
    session: Session, viewer: Viewer, lead_id: uuid.UUID, team_id: uuid.UUID, clock: Clock
) -> Done:
    """Unassigned to a team's pool. Arrival stays, so time spent in Unassigned counts."""
    lead = _locked(session, viewer, lead_id, "place")
    event = _check(lead, "place", "pool", relations(viewer, ref(lead)))
    team = _team_in_org(session, lead, team_id)
    payload = {"by": viewer.user_id, "team": team.slug}
    return _to_pool(session, lead, event, "place", payload, clock, team.id)


def reassign(
    session: Session, viewer: Viewer, lead_id: uuid.UUID, to_user: uuid.UUID, clock: Clock
) -> Done:
    lead = _locked(session, viewer, lead_id, "reassign")
    event = _check(lead, "reassign", lead.state, relations(viewer, ref(lead)))
    member = session.scalar(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(User.id == to_user, User.active, Membership.team_id == lead.team_id)
    )
    if member is None:
        raise InvalidError("the new owner must be an active member of the lead's team")
    if member.id == lead.owner_id:
        raise InvalidError("that user already owns the lead")
    now = clock.now()
    payload = {"by": viewer.user_id, "from": str(lead.owner_id), "to": str(member.id)}
    lead.owner_id = member.id
    cause = _cause("reassign")
    events.write(session, lead, event, cause, payload, now)
    alerts.queue(session, lead, "assigned", cause, [member], now)
    return Done(lead.id, event)


def owner_left(
    session: Session, user_id: uuid.UUID, clock: Clock, team_id: uuid.UUID | None = None
) -> list[Done]:
    """Return the user's open owned leads to their pools: every team when the user was
    deactivated, or one team when they were removed from it. Each pool is alerted."""
    query = select(Lead).where(Lead.owner_id == user_id, Lead.state.in_(states.OWNED))
    if team_id is not None:
        query = query.where(Lead.team_id == team_id)
    done: list[Done] = []
    for lead in session.scalars(query.order_by(Lead.id).with_for_update()):
        event = states.check("owner_left", lead.state, "pool", frozenset({"system"}))
        payload = {"reason": "owner_left", "owner_id": str(user_id)}
        done.append(_to_pool(session, lead, event, "owner_left", payload, clock))
    return done


def allowed_actions(viewer: Viewer, lead: Lead) -> list[str]:
    """The actions this viewer could take on the lead now, for the page's buttons."""
    rels = relations(viewer, ref(lead))
    targets = {
        "claim": "owned",
        "release": "pool",
        "return": "pool",
        "reassign": lead.state,
        "move_team": "pool",
        "place": "pool",
    }
    found: list[str] = []
    for action, target in targets.items():
        try:
            states.check(action, lead.state, target, rels, lead.team_id is None)
        except states.RefusedError:
            continue
        found.append(action)
    return found


def visible(viewer: Viewer, lead: Lead) -> bool:
    return str(lead.org_id) == viewer.org_id and can_see(viewer, ref(lead))
