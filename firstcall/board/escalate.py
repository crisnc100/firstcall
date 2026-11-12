"""The escalation pass, run by the scheduler's tick."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import alerts
from firstcall.board.leads import team_hours
from firstcall.core import escalation
from firstcall.core.clock import Clock
from firstcall.db import Escalation, Lead, Team, User


def _audience(session: Session, lead: Lead, level: escalation.Level) -> list[User]:
    if level.audience == "team_leads":
        assert lead.team_id is not None
        return alerts.team_leads(session, lead.team_id)
    if level.audience == "admins_and_monitors":
        return alerts.with_role(session, lead.org_id, "admin", "monitor")
    return alerts.with_role(session, lead.org_id, "admin")


def escalate(sessions: sessionmaker[Any], clock: Clock) -> int:
    """Fire every level that is due. Each (lead, level) is stored once, so a second tick, or
    two schedulers at once, alert nobody twice. Returns how many levels fired."""
    now = clock.now()
    fired_count = 0
    with sessions.begin() as session:
        teams = {t.id: t for t in session.scalars(select(Team))}
        hours = {team_id: team_hours(team) for team_id, team in teams.items()}
        pool = session.scalars(
            select(Lead)
            .where(Lead.state == "pool")
            .order_by(Lead.arrival_at)
            .with_for_update(skip_locked=True)
        )
        for lead in pool:
            fired = frozenset(
                session.scalars(select(Escalation.level).where(Escalation.lead_id == lead.id))
            )
            team = teams.get(lead.team_id) if lead.team_id else None
            overrides = {str(k): float(v) for k, v in (team.escalation if team else {}).items()}
            due = escalation.due(
                lead.state,
                team is not None,
                lead.arrival_at,
                now,
                hours.get(lead.team_id) if lead.team_id else None,
                fired,
                overrides,
            )
            for level in due:
                stored = session.scalar(
                    insert(Escalation)
                    .values(
                        id=uuid.uuid4(),
                        org_id=lead.org_id,
                        lead_id=lead.id,
                        level=level.name,
                        fired_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=["lead_id", "level"])
                    .returning(Escalation.id)
                )
                if stored is None:
                    continue
                fired_count += 1
                kind = f"escalation:{level.name}"
                alerts.queue(session, lead, kind, kind, _audience(session, lead, level), now)
    return fired_count
