"""Queue alerts. Nothing is sent here: the outbound dispatcher sends after the commit.

An alert is unique on (lead, kind, recipient, cause), so processing a message again, or a
second scheduler tick, queues nothing new. Each alert gets one push send per device the user
has when it is queued.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from firstcall.db import Alert, Lead, Membership, PushSend, PushSubscription, User


def _active(session: Session, org_id: uuid.UUID) -> list[User]:
    return list(session.scalars(select(User).where(User.org_id == org_id, User.active)))


def pool_members(session: Session, org_id: uuid.UUID, team_id: uuid.UUID | None) -> list[User]:
    """Who a pool belongs to: a team's members, or the monitors for Unassigned."""
    if team_id is None:
        return [u for u in _active(session, org_id) if "monitor" in u.roles]
    return list(
        session.scalars(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.team_id == team_id, User.active)
            .order_by(User.email)
        )
    )


def team_leads(session: Session, team_id: uuid.UUID) -> list[User]:
    return list(
        session.scalars(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.team_id == team_id, Membership.role == "team_lead", User.active)
        )
    )


def with_role(session: Session, org_id: uuid.UUID, *roles: str) -> list[User]:
    return [u for u in _active(session, org_id) if set(roles) & set(u.roles)]


def queue(
    session: Session, lead: Lead, kind: str, cause: str, users: Iterable[User], now: datetime
) -> int:
    """Queue one alert per user. Returns how many were new."""
    made = 0
    for user in {u.id: u for u in users}.values():
        alert_id = session.scalar(
            insert(Alert)
            .values(
                id=uuid.uuid4(),
                org_id=lead.org_id,
                lead_id=lead.id,
                user_id=user.id,
                team_id=lead.team_id,
                kind=kind,
                cause=cause,
                created_at=now,
                email_state="pending",
            )
            .on_conflict_do_nothing(index_elements=["lead_id", "kind", "user_id", "cause"])
            .returning(Alert.id)
        )
        if alert_id is None:
            continue
        made += 1
        devices = session.scalars(
            select(PushSubscription.id).where(PushSubscription.user_id == user.id)
        )
        for subscription_id in devices:
            session.add(
                PushSend(
                    org_id=lead.org_id,
                    alert_id=alert_id,
                    subscription_id=subscription_id,
                    state="pending",
                    attempts=0,
                )
            )
    return made


def entered_pool(session: Session, lead: Lead, kind: str, cause: str, now: datetime) -> int:
    """A lead entered a pool: tell every member of that pool, and nobody outside it."""
    return queue(session, lead, kind, cause, pool_members(session, lead.org_id, lead.team_id), now)


def activity(session: Session, lead: Lead, cause: str, now: datetime) -> int:
    """New activity alerts only the owner. A pool lead's activity sends nothing fresh."""
    if lead.owner_id is None or lead.state not in ("owned", "nurture"):
        return 0
    owner = session.get(User, lead.owner_id)
    if owner is None or not owner.active:
        return 0
    return queue(session, lead, "activity", cause, [owner], now)
