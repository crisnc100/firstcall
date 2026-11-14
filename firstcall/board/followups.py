"""Follow-ups: the nightly job that opens them, and the touches and moves that close them.

Each team runs once per local date, from 05:00 its time, through `firstcall tick`. A run
row and every follow-up it changes commit together, so a crash leaves nothing half-done and
the next tick redoes the run. Each lead runs in its own savepoint: one lead that raises is
logged, counted and skipped, and cannot stop its team's run. The job makes no model call.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from firstcall import events, failpoints
from firstcall.core import cadence
from firstcall.core.clock import Clock
from firstcall.core.hours import Hours, hours_from
from firstcall.db import Contact, Followup, FollowupRun, Lead, LeadProfile, Outcome, Team

log = logging.getLogger("firstcall.followups")

RUN_FROM = time(5, 0)
FAILPOINT_DURING_RUN = "during_followup_run"


def open_followup(session: Session, lead_id: uuid.UUID) -> Followup | None:
    return session.scalar(
        select(Followup)
        .where(Followup.lead_id == lead_id, Followup.state == "open")
        .with_for_update()
    )


def touched(session: Session, lead: Lead, at: datetime) -> None:
    """A real contact closes the lead's open follow-up as done. The caller holds the lead's
    row lock."""
    found = open_followup(session, lead.id)
    if found is None:
        return
    found.state, found.closed_at = "done", at
    payload = {"followup_id": str(found.id)}
    events.write(session, lead, "followup.done", f"followup:{found.id}:done", payload, at)


def cancel(session: Session, lead: Lead, now: datetime) -> None:
    """The lead left its owner (back to a pool, or closed): its open follow-up is cancelled,
    with an event, so a CRM reading the events never keeps the task open."""
    found = open_followup(session, lead.id)
    if found is None:
        return
    found.state, found.closed_at = "cancelled", now
    payload = {"followup_id": str(found.id), "to": lead.state}
    events.write(
        session, lead, "followup.cancelled", f"followup:{found.id}:cancelled", payload, now
    )


def moved(session: Session, lead: Lead) -> None:
    """A reassign hands the open follow-up to the new owner."""
    found = open_followup(session, lead.id)
    if found is not None and lead.owner_id is not None:
        found.owner_id = lead.owner_id


def _local(moment: datetime, hours: Hours) -> date:
    return moment.astimezone(hours.zone).date()


def _known(profile: LeadProfile | None) -> frozenset[str]:
    if profile is None:
        return frozenset()
    found: set[str] = set()
    if profile.budget_text or profile.budget_min is not None or profile.budget_max is not None:
        found.add("budget")
    if profile.authority != "unknown":
        found.add("authority")
    if profile.need:
        found.add("need")
    if profile.timeline_text or profile.timeline_month:
        found.add("timeline")
    return frozenset(found)


def follow_lead(
    session: Session, lead: Lead, today: date, now: datetime, plan: cadence.Cadence, hours: Hours
) -> bool:
    """Open or move the lead's follow-up if a step is due. True when one was opened."""
    last = session.scalar(select(func.max(Contact.at)).where(Contact.lead_id == lead.id))
    last_touch = _local(last, hours) if last is not None else None
    nurture_at = session.scalar(
        select(func.max(Outcome.at)).where(Outcome.lead_id == lead.id, Outcome.outcome == "nurture")
    )
    step = cadence.current_step(
        lead.state,
        _local(lead.arrival_at, hours),
        today,
        last_touch,
        _local(nurture_at, hours) if nurture_at is not None else None,
        plan,
        hours,
    )
    if step is None or not cadence.is_due(step, last_touch):
        return False
    profile = session.scalar(select(LeadProfile).where(LeadProfile.lead_id == lead.id))
    line = cadence.next_step(today, _local(lead.arrival_at, hours), last_touch, _known(profile))
    assert lead.owner_id is not None
    found = open_followup(session, lead.id)
    if found is not None:
        # One open follow-up per lead: a later run moves it to the newest due step.
        if found.due_on < step.due_on:
            found.due_on, found.step = step.due_on, step.label
        found.next_step, found.owner_id = line, lead.owner_id
        return False
    followup = Followup(
        id=uuid.uuid4(), org_id=lead.org_id, lead_id=lead.id, owner_id=lead.owner_id,
        due_on=step.due_on, step=step.label, next_step=line, state="open", created_at=now,
    )  # fmt: skip
    session.add(followup)
    session.flush()
    payload = {"followup_id": str(followup.id), "owner_id": str(lead.owner_id)}
    events.write(session, lead, "followup.created", f"followup:{followup.id}", payload, now)
    return True


def _rules(team: Team) -> tuple[Hours, cadence.Cadence]:
    return hours_from(team.business_hours, team.timezone), cadence.cadence_from(team.cadence)


def run_nightly(sessions: sessionmaker[Any], clock: Clock) -> dict[str, int]:
    """Run every team whose local time has reached 05:00 and that has not run today."""
    now = clock.now()
    done = {"teams": 0, "opened": 0, "errors": 0}
    with sessions() as session:
        teams = list(session.scalars(select(Team).order_by(Team.slug)))
    for team in teams:
        try:
            hours, plan = _rules(team)
        except (ValueError, KeyError, TypeError) as error:
            log.error("team %s skipped by the follow-up job: %s", team.slug, error)
            done["errors"] += 1
            continue
        local = now.astimezone(hours.zone)
        if local.time() < RUN_FROM:
            continue
        ran = _run_team(sessions, team, local.date(), now, plan, hours)
        if ran is not None:
            done["teams"] += 1
            done["opened"] += ran.opened
            done["errors"] += ran.errors
    return done


def _run_team(
    sessions: sessionmaker[Any],
    team: Team,
    today: date,
    now: datetime,
    plan: cadence.Cadence,
    hours: Hours,
) -> FollowupRun | None:
    with sessions.begin() as session:
        run_id = session.scalar(
            insert(FollowupRun)
            .values(
                id=uuid.uuid4(),
                org_id=team.org_id,
                team_id=team.id,
                local_date=today,
                ran_at=now,
                leads=0,
                opened=0,
                errors=0,
            )
            .on_conflict_do_nothing(index_elements=["team_id", "local_date"])
            .returning(FollowupRun.id)
        )
        if run_id is None:
            return None
        run = session.get_one(FollowupRun, run_id)
        leads = session.scalars(
            select(Lead)
            .where(
                Lead.team_id == team.id,
                Lead.state.in_(("owned", "nurture")),
                Lead.deleted_at.is_(None),
            )
            .order_by(Lead.id)
            .with_for_update()
        )
        for lead in leads:
            run.leads += 1
            try:
                with session.begin_nested():
                    run.opened += follow_lead(session, lead, today, now, plan, hours)
            except Exception:
                log.exception("lead %s skipped by the follow-up job", lead.id)
                run.errors += 1
            failpoints.fire(FAILPOINT_DURING_RUN)
        return run
