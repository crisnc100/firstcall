"""The escalation pass, run by the scheduler's tick."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import alerts
from firstcall.board.leads import team_hours
from firstcall.core import escalation
from firstcall.core.clock import Clock
from firstcall.core.hours import Hours
from firstcall.db import Escalation, Lead, Team, User

log = logging.getLogger("firstcall.escalation")


def _audience(session: Session, lead: Lead, level: escalation.Level) -> list[User]:
    if level.audience == "team_leads":
        assert lead.team_id is not None
        return alerts.team_leads(session, lead.team_id)
    if level.audience == "admins_and_monitors":
        return alerts.with_role(session, lead.org_id, "admin", "monitor")
    return alerts.with_role(session, lead.org_id, "admin")


def _team_rules(team: Team) -> tuple[Hours, dict[str, float]]:
    return team_hours(team), escalation.overrides_from(team.escalation or {})


def escalate(sessions: sessionmaker[Any], clock: Clock) -> int:
    """Fire every level that is due. Each (lead, level, stay) is stored once, so a second
    tick, or two schedulers at once, alert nobody twice. A team whose hours or minutes do
    not read is logged and skipped, so one bad config cannot stop the others. Returns how
    many levels fired."""
    now = clock.now()
    fired_count = 0
    with sessions.begin() as session:
        rules: dict[uuid.UUID, tuple[Hours, dict[str, float]]] = {}
        for team in session.scalars(select(Team)):
            try:
                rules[team.id] = _team_rules(team)
            except (ValueError, KeyError, TypeError) as error:
                log.error("team %s skipped by escalation: %s", team.slug, error)
        pool = session.scalars(
            select(Lead)
            .where(Lead.state == "pool")
            .order_by(Lead.arrival_at)
            .with_for_update(skip_locked=True)
        )
        for lead in pool:
            if lead.team_id is not None and lead.team_id not in rules:
                continue
            hours, overrides = rules[lead.team_id] if lead.team_id else (None, {})
            stay = lead.pool_since or lead.arrival_at
            fired = frozenset(
                session.scalars(
                    select(Escalation.level).where(
                        Escalation.lead_id == lead.id, Escalation.stay == stay
                    )
                )
            )
            due = escalation.due(
                lead.state, lead.team_id is not None, stay, now, hours, fired, overrides
            )
            for level in due:
                stored = session.scalar(
                    insert(Escalation)
                    .values(
                        id=uuid.uuid4(),
                        org_id=lead.org_id,
                        lead_id=lead.id,
                        level=level.name,
                        stay=stay,
                        fired_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=["lead_id", "level", "stay"])
                    .returning(Escalation.id)
                )
                if stored is None:
                    continue
                fired_count += 1
                kind = f"escalation:{level.name}"
                cause = f"{kind}:{stay.isoformat()}"
                alerts.queue(session, lead, kind, cause, _audience(session, lead, level), now)
    return fired_count
