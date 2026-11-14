"""Lead actions on the board: claim, release, return, reassign, move team, place, owner left.

Each action locks the lead row, asks the state machine, changes the lead, writes one lead
event and queues its alerts, all in the caller's transaction. A viewer who cannot see the
lead, or whose role has no row for the action, gets NotFoundError so the answer is the
same as for a lead that does not exist. A permitted action on a lead in the wrong state
gets ConflictError: that is how the second of two claims learns it lost.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from firstcall import events
from firstcall.board import alerts, followups
from firstcall.core import escalation, states
from firstcall.core.access import LeadRef, Viewer, can_see, relations
from firstcall.core.clock import Clock
from firstcall.core.hours import Hours, business_minutes, hours_from, wall_minutes
from firstcall.core.outcomes import check_outcome, may_log_contact, may_note
from firstcall.db import Lead, Membership, Outcome, Team, User

log = logging.getLogger("firstcall.board")


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


def lock_user(session: Session, user_id: uuid.UUID, share: bool) -> bool | None:
    """Lock a user row and read whether they are active now; None when there is no user.

    Every path takes the user row before any lead row: claim and reassign share-lock the
    user who will own the lead, while deactivating and changing teams take FOR NO KEY
    UPDATE. So a claim either finishes first and its lead is then returned by owner-left,
    or waits and sees the user gone. A lead never ends owned by an inactive user or a
    non-member. A plain FOR UPDATE would also block the key-share lock that every alert
    insert takes on its user, stalling alerts behind a team change."""
    locked = select(User.active).where(User.id == user_id)
    if share:
        return session.scalar(locked.with_for_update(read=True))
    return session.scalar(locked.with_for_update(key_share=True))


def _member(session: Session, user_id: uuid.UUID, team_id: uuid.UUID | None) -> bool:
    if team_id is None:
        return False
    found = session.scalar(
        select(Membership.id).where(Membership.user_id == user_id, Membership.team_id == team_id)
    )
    return found is not None


def _locked(session: Session, viewer: Viewer, lead_id: uuid.UUID, action: str) -> Lead:
    lead = session.get(Lead, lead_id, with_for_update=True)
    if lead is None or str(lead.org_id) != viewer.org_id or lead.deleted_at is not None:
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
    user_id = uuid.UUID(viewer.user_id)
    if not lock_user(session, user_id, share=True):
        raise NotFoundError
    lead = _locked(session, viewer, lead_id, "claim")
    if not _member(session, user_id, lead.team_id):
        raise NotFoundError
    event = _check(lead, "claim", "owned", _relations(viewer, lead, "claim"))
    now = clock.now()
    lead.state, lead.owner_id, lead.pool_since = "owned", user_id, None
    lead.owned_since = now
    if lead.uncontacted_since is None:
        lead.uncontacted_since = now
    if lead.claimed_at is None:
        assert lead.team_id is not None
        team = session.get_one(Team, lead.team_id)
        lead.claimed_at = now
        lead.claim_minutes = _minutes(wall_minutes(lead.arrival_at, now))
        try:
            hours = team_hours(team)
        except (ValueError, KeyError, TypeError) as error:
            # A claim must never fail over a config the claimer cannot fix; the
            # business-minute report skips this lead instead.
            log.error(
                "team %s hours do not read; claim kept without business minutes: %s",
                team.slug,
                error,
            )
        else:
            lead.claim_business_minutes = _minutes(business_minutes(lead.arrival_at, now, hours))
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
    lead.owned_since = None
    followups.cancel(session, lead, now)
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
    target_active = lock_user(session, to_user, share=True)
    lead = _locked(session, viewer, lead_id, "reassign")
    event = _check(lead, "reassign", lead.state, relations(viewer, ref(lead)))
    member = session.get(User, to_user)
    if member is None or not target_active or not _member(session, to_user, lead.team_id):
        raise InvalidError("the new owner must be an active member of the lead's team")
    if member.id == lead.owner_id:
        raise InvalidError("that user already owns the lead")
    now = clock.now()
    payload = {"by": viewer.user_id, "from": str(lead.owner_id), "to": str(member.id)}
    lead.owner_id, lead.owned_since = member.id, now
    followups.moved(session, lead)
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


def urgency(lead: Lead, team: Team | None, now: datetime) -> dict[str, Any]:
    """How long a pool lead has waited in its current pool stay, in its team's business
    minutes, with the minutes at which its team escalates: the same clock and thresholds
    escalation uses, so a page shows "Late" exactly when escalation does. Time to claim
    still counts from arrival. Unassigned has no hours, so its wait is wall-clock. Nothing
    for a lead not in a pool, or for a team whose config does not read."""
    if lead.state != "pool":
        return {"wait_business_minutes": None, "thresholds": None}
    since = lead.pool_since or lead.arrival_at
    if team is None:
        waited = wall_minutes(since, now)
        late, very_late = (
            escalation.DEFAULTS[escalation.UNASSIGNED],
            escalation.DEFAULTS[escalation.ADMINS],
        )
    else:
        try:
            hours, minutes = team_hours(team), escalation.overrides_from(team.escalation or {})
        except (ValueError, KeyError, TypeError):
            return {"wait_business_minutes": None, "thresholds": None}
        waited = business_minutes(since, now, hours)
        late = minutes.get(escalation.TEAM_LEADS, escalation.DEFAULTS[escalation.TEAM_LEADS])
        very_late = minutes.get(escalation.ADMINS, escalation.DEFAULTS[escalation.ADMINS])
    return {
        # Rounded down, so the label never turns Late a moment before the timer's minute.
        "wait_business_minutes": math.floor(waited * 10) / 10,
        "thresholds": {"late": late, "very_late": very_late},
    }


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
    return _permitted(rels, lead, targets)


def _permitted(rels: frozenset[states.Relation], lead: Lead, targets: dict[str, str]) -> list[str]:
    found: list[str] = []
    for action, target in targets.items():
        try:
            states.check(action, lead.state, target, rels, lead.team_id is None)
        except states.RefusedError:
            continue
        found.append(action)
    return found


def work_actions(viewer: Viewer, lead: Lead) -> list[str]:
    """What the viewer may do to work the lead: notes, contacts, outcomes, reopen and
    delete. Kept apart from the board moves in allowed_actions."""
    rels = relations(viewer, ref(lead))
    targets = {"set_nurture": "nurture", "resume": "owned", "set_outcome": "won",
               "reopen": "owned"}  # fmt: skip
    found = _permitted(rels, lead, targets)
    if may_note(viewer, ref(lead)):
        found.append("note")
    if may_log_contact(viewer, ref(lead)):
        found.append("log_contact")
    if viewer.admin:
        found.append("delete")
    return found


def visible(viewer: Viewer, lead: Lead) -> bool:
    return (
        lead.deleted_at is None and str(lead.org_id) == viewer.org_id and can_see(viewer, ref(lead))
    )


def set_nurture(session: Session, viewer: Viewer, lead_id: uuid.UUID, clock: Clock) -> Done:
    """Owned to nurture: the lead stays open on a monthly rhythm. Recorded as an outcome."""
    lead = _locked(session, viewer, lead_id, "set_nurture")
    event = _check(lead, "set_nurture", "nurture", relations(viewer, ref(lead)))
    now = clock.now()
    lead.state = "nurture"
    _record_outcome(session, lead, "nurture", None, None, None, viewer, now)
    payload = {"by": viewer.user_id, "outcome": "nurture"}
    events.write(session, lead, event, _cause("set_nurture"), payload, now)
    return Done(lead.id, event)


def resume(session: Session, viewer: Viewer, lead_id: uuid.UUID, clock: Clock) -> Done:
    lead = _locked(session, viewer, lead_id, "resume")
    event = _check(lead, "resume", "owned", relations(viewer, ref(lead)))
    now = clock.now()
    lead.state = "owned"
    payload = {"by": viewer.user_id, "reason": "resumed", "to": "owned"}
    events.write(session, lead, event, _cause("resume"), payload, now)
    return Done(lead.id, event)


def set_outcome(
    session: Session,
    viewer: Viewer,
    lead_id: uuid.UUID,
    outcome: str,
    reason: str | None,
    reason_text: str | None,
    competitor: str | None,
    clock: Clock,
) -> Done:
    lead = _locked(session, viewer, lead_id, "set_outcome")
    try:
        check_outcome(outcome, reason, reason_text)
    except ValueError as error:
        raise InvalidError(str(error)) from error
    event = _check(lead, "set_outcome", outcome, relations(viewer, ref(lead)))
    now = clock.now()
    lead.state = outcome
    followups.cancel(session, lead, now)
    _record_outcome(session, lead, outcome, reason, reason_text, competitor, viewer, now)
    payload = {"by": viewer.user_id, "outcome": outcome, "reason": reason}
    events.write(session, lead, event, _cause("set_outcome"), payload, now)
    return Done(lead.id, event)


def _record_outcome(
    session: Session,
    lead: Lead,
    outcome: str,
    reason: str | None,
    reason_text: str | None,
    competitor: str | None,
    viewer: Viewer,
    now: datetime,
) -> None:
    session.add(
        Outcome(
            id=uuid.uuid4(),
            org_id=lead.org_id,
            lead_id=lead.id,
            outcome=outcome,
            reason=reason,
            reason_text=(reason_text or "").strip() or None if reason == "other" else None,
            competitor=(competitor or "").strip()[:200] or None if outcome == "lost" else None,
            by=uuid.UUID(viewer.user_id),
            at=now,
        )
    )


def lock_owner(session: Session, lead_id: uuid.UUID) -> tuple[uuid.UUID | None, bool]:
    """Share-lock a lead's current owner before the lead row, the order every path keeps
    (see lock_user). Returns the owner read and whether they are active."""
    owner_id = session.scalar(select(Lead.owner_id).where(Lead.id == lead_id))
    if owner_id is None:
        return None, False
    return owner_id, bool(lock_user(session, owner_id, share=True))


def owner_still_here(session: Session, lead: Lead, locked: tuple[uuid.UUID | None, bool]) -> bool:
    """Whether the lead's owner, locked by lock_owner, is still active and in its team."""
    owner_id, active = locked
    if lead.owner_id is None or lead.owner_id != owner_id or not active:
        return False
    return _member(session, lead.owner_id, lead.team_id)


def reopen_to(
    session: Session,
    lead: Lead,
    owner_ok: bool,
    event: str,
    cause: str,
    payload: dict[str, Any],
    now: datetime,
) -> str:
    """Reopen a closed lead to its owner when they can take it back, else to its pool. A
    reopen starts a new uncontacted stretch. Returns the state it reopened to."""
    lead.uncontacted_since = now
    if owner_ok:
        lead.state = "owned"
        events.write(session, lead, event, cause, {**payload, "to": "owned"}, now)
        return "owned"
    lead.state, lead.owner_id, lead.pool_since, lead.owned_since = "pool", None, now, None
    events.write(session, lead, event, cause, {**payload, "to": "pool"}, now)
    alerts.entered_pool(session, lead, "reopened", cause, now)
    return "pool"


def reopen(session: Session, viewer: Viewer, lead_id: uuid.UUID, clock: Clock) -> Done:
    """A team lead's or admin's manual reopen of a won, lost or not-real lead."""
    owner = lock_owner(session, lead_id)
    lead = _locked(session, viewer, lead_id, "reopen")
    rels = relations(viewer, ref(lead))
    event = _check(lead, "reopen", "owned", rels)
    owner_ok = owner_still_here(session, lead, owner)
    payload = {"by": viewer.user_id}
    reopen_to(session, lead, owner_ok, event, _cause("reopen"), payload, clock.now())
    return Done(lead.id, event)
