"""Send queued alerts: push at once, email as a digest.

Rows are locked with SKIP LOCKED while they are sent, so two workers never send the same
one. A crash between the send and the commit sends it again on the next pass: a repeat
alert is better than a lost one. A failed send never escapes these passes: it is logged
and counted against that send, so the worker and scheduler keep running.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts, leads
from firstcall.core.clock import Clock
from firstcall.db import Alert, Lead, PushSend, PushSubscription, Team, User
from firstcall.outbound import allowlist
from firstcall.outbound.mail import InboxMailer, Line, Mailer, SmtpMailer, label, render
from firstcall.outbound.push import NO_RESPONSE, PushSender, WebPushSender
from firstcall.settings import Settings

log = logging.getLogger("firstcall.outbound")
GONE = (404, 410)
BATCH = 100


def retryable(status: int) -> bool:
    return status >= 500 or status == NO_RESPONSE


def tag(team: Team | None) -> str:
    """One notification tag per team, so a device shows one notification for a burst."""
    return f"team-{team.slug}" if team else "unassigned"


@dataclass
class Outbound:
    sessions: sessionmaker[Any]
    clock: Clock
    settings: Settings
    mailer: Mailer
    pusher: PushSender | None

    @classmethod
    def from_settings(
        cls, sessions: sessionmaker[Any], clock: Clock, settings: Settings
    ) -> Outbound:
        mailer: Mailer = (
            SmtpMailer(settings.smtp_host, settings.smtp_port, settings.email_from)
            if settings.email_backend == "smtp"
            else InboxMailer()
        )
        key = settings.vapid_private_key.get_secret_value()
        pusher = WebPushSender(key, settings.vapid_subject) if key else None
        return cls(sessions, clock, settings, mailer, pusher)

    def lead_url(self, lead_id: uuid.UUID) -> str:
        return f"{self.settings.base_url.rstrip('/')}/leads/{lead_id}"

    def push_pending(self) -> int:
        """Send every pending push once. Returns how many were tried."""
        if self.pusher is None:
            return 0
        try:
            with self.sessions.begin() as session:
                sends = list(
                    session.scalars(
                        select(PushSend)
                        .where(PushSend.state == "pending")
                        .order_by(PushSend.id)
                        .limit(BATCH)
                        .with_for_update(skip_locked=True)
                    )
                )
                for send in sends:
                    self._push_contained(session, send)
                return len(sends)
        except Exception:
            log.exception("the push pass failed; pending pushes wait for the next pass")
            return 0

    def _push_contained(self, session: Session, send: PushSend) -> None:
        """One send in its own savepoint, so an error rolls back only that send's changes
        and counts as a failed attempt."""
        try:
            with session.begin_nested():
                self._push(session, send)
        except Exception:
            log.exception("push send %s raised", send.id)
            send.attempts += 1
            send.last_status = NO_RESPONSE
            if send.attempts > self.settings.push_retries:
                send.state = "failed"

    def _push(self, session: Session, send: PushSend) -> None:
        assert self.pusher is not None
        alert = session.get_one(Alert, send.alert_id)
        subscription = (
            session.get(PushSubscription, send.subscription_id) if send.subscription_id else None
        )
        if subscription is None:
            send.state = "gone"
            return
        try:
            url = allowlist.check_push(
                session,
                subscription,
                alert.user_id,
                self.settings.push_allow_http,
                self.settings.push_hosts,
            )
        except allowlist.RefusedError:
            send.state = "failed"
            return
        lead = session.get_one(Lead, alert.lead_id)
        if not _still_sees(session, alert.user_id, lead):
            send.state = "failed"
            return
        team = session.get(Team, alert.team_id) if alert.team_id else None
        payload = {
            "title": f"{label(alert.kind)} in {team.name if team else 'Unassigned'}",
            "body": " ".join(filter(None, [lead.name, lead.item])) or "Open Firstcall to see it",
            "tag": tag(team),
            "url": self.lead_url(lead.id),
        }
        send.attempts += 1
        status = self.pusher.send(url, subscription.p256dh, subscription.auth, payload)
        send.last_status = status
        if 200 <= status < 300:
            send.state, send.sent_at = "sent", self.clock.now()
        elif status in GONE:
            send.state = "gone"
            session.delete(subscription)
        elif retryable(status) and send.attempts <= self.settings.push_retries:
            log.info("push answered %s; will retry", status)
        else:
            send.state = "failed"

    def email_digests(self) -> int:
        """One email per user and team once the oldest pending alert in that pair has waited
        the digest window. A failed send is counted on its alerts and tried again after a
        doubling wait; past the retry limit they are marked failed. Returns how many emails
        went out."""
        now = self.clock.now()
        cutoff = now - timedelta(seconds=self.settings.digest_seconds)
        try:
            with self.sessions() as session:
                groups = session.execute(
                    select(Alert.user_id, Alert.team_id)
                    .where(Alert.email_state == "pending", _due(now))
                    .group_by(Alert.user_id, Alert.team_id)
                    .having(func.min(Alert.created_at) <= cutoff)
                ).all()
        except Exception:
            log.exception("the digest pass failed; pending email waits for the next pass")
            return 0
        sent = 0
        for user_id, team_id in groups:
            try:
                with self.sessions.begin() as session:
                    sent += self._digest(session, user_id, team_id)
            except Exception:
                log.exception("digest for user %s failed", user_id)
                self._count_failure(user_id, team_id, now)
        return sent

    def _count_failure(self, user_id: uuid.UUID, team_id: uuid.UUID | None, now: datetime) -> None:
        try:
            with self.sessions.begin() as session:
                alerts = list(
                    session.scalars(
                        select(Alert)
                        .where(
                            Alert.email_state == "pending",
                            Alert.user_id == user_id,
                            _same_team(team_id),
                            _due(now),
                        )
                        .with_for_update(skip_locked=True)
                    )
                )
                for alert in alerts:
                    alert.email_attempts += 1
                    if alert.email_attempts > self.settings.email_retries:
                        alert.email_state, alert.email_retry_at = "failed", None
                    else:
                        wait = self.settings.digest_seconds * 2**alert.email_attempts
                        alert.email_retry_at = now + timedelta(seconds=wait)
        except Exception:
            # Uncounted, the alerts stay pending and due, so the next pass tries again.
            log.exception("could not count the failed digest for user %s", user_id)

    def _digest(self, session: Session, user_id: uuid.UUID, team_id: uuid.UUID | None) -> int:
        alerts = list(
            session.scalars(
                select(Alert)
                .where(
                    Alert.email_state == "pending",
                    Alert.user_id == user_id,
                    _same_team(team_id),
                    _due(self.clock.now()),
                )
                .order_by(Alert.created_at, Alert.id)
                .with_for_update(skip_locked=True)
            )
        )
        if not alerts:
            return 0
        user = session.get_one(User, user_id)
        try:
            allowlist.check_email(session, user.org_id, user.email)
        except allowlist.RefusedError:
            for alert in alerts:
                alert.email_state = "refused"
            return 0
        team = session.get(Team, team_id) if team_id else None
        lines: list[Line] = []
        kept: list[Alert] = []
        for alert in alerts:
            lead = session.get_one(Lead, alert.lead_id)
            if not _still_sees(session, user.id, lead):
                alert.email_state = "refused"
                continue
            kept.append(alert)
            name = lead.name or "Name not known"
            lines.append(Line(alert.kind, name, lead.item, self.lead_url(lead.id), _path(lead.id)))
        if not lines:
            return 0
        now = self.clock.now()
        self.mailer.send(session, user, render(team.name if team else "Unassigned", lines), now)
        for alert in kept:
            alert.email_state, alert.email_sent_at = "sent", now
        return 1


def _same_team(team_id: uuid.UUID | None) -> Any:
    return Alert.team_id.is_(None) if team_id is None else Alert.team_id == team_id


def _due(now: datetime) -> Any:
    return or_(Alert.email_retry_at.is_(None), Alert.email_retry_at <= now)


def _still_sees(session: Session, user_id: uuid.UUID, lead: Lead) -> bool:
    """Whether the recipient may see the lead now. An alert is queued when it is made, so a
    recipient removed from the team since must not get the lead's current details."""
    user = session.get_one(User, user_id)
    return user.active and leads.visible(accounts.viewer(session, user), lead)


def _path(lead_id: uuid.UUID) -> str:
    return f"/leads/{lead_id}"
