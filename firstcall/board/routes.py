"""The board's JSON API: sign-in, pools, lead actions, the tray, alerts and users.

Every route but sign-in needs a session cookie, and every route that changes state needs
the session's CSRF token in the X-CSRF-Token header. A lead, message or user the caller may
not see or act on answers 404, exactly as if it did not exist.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, NoReturn

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, StrictInt
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts, delete, leads, notes, tray
from firstcall.board.leads import ConflictError, InvalidError, NotFoundError
from firstcall.core import escalation
from firstcall.core.access import Viewer, can_see_team
from firstcall.core.clock import Clock
from firstcall.core.outcomes import may_read_notes
from firstcall.core.read import Reference, UnparseableError, read
from firstcall.db import (
    Contact,
    Escalation,
    Followup,
    InboxEmail,
    Lead,
    LeadEvent,
    Membership,
    Message,
    Note,
    Outcome,
    PushSubscription,
    Source,
    Team,
    User,
    UserSession,
)
from firstcall.intake.limits import RateLimiter
from firstcall.outbound.push import endpoint_allowed
from firstcall.pipeline.process import source_rules
from firstcall.settings import Settings

SESSION_COOKIE = "fc_session"
CSRF_COOKIE = "fc_csrf"
CSRF_HEADER = "x-csrf-token"
LIST_LIMIT = 200

log = logging.getLogger("firstcall.board")


class SignIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1000)


class ToUser(BaseModel):
    user_id: uuid.UUID


class ToTeam(BaseModel):
    team_id: uuid.UUID


class NewNote(BaseModel):
    text: str = Field(max_length=5000)
    action: str | None = None


class SetOutcome(BaseModel):
    outcome: str
    reason: str | None = None
    reason_text: str | None = Field(default=None, max_length=1000)
    competitor: str | None = Field(default=None, max_length=200)


class ProfileChange(BaseModel):
    budget_text: str | None = Field(default=None, max_length=1000)
    budget_min: StrictInt | None = None
    budget_max: StrictInt | None = None
    authority: str | None = None
    authority_others: str | None = Field(default=None, max_length=1000)
    need: str | None = Field(default=None, max_length=2000)
    timeline_text: str | None = Field(default=None, max_length=1000)
    timeline_month: str | None = Field(default=None, max_length=7)
    notes: str | None = Field(default=None, max_length=5000)


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class Subscription(BaseModel):
    endpoint: str = Field(max_length=2000)
    keys: PushKeys


class Unsubscribe(BaseModel):
    endpoint: str = Field(max_length=2000)


class NewUser(BaseModel):
    email: str = Field(max_length=320)
    name: str = Field(max_length=200)
    roles: list[str] = []
    teams: dict[uuid.UUID, str] = {}
    password: str | None = Field(default=None, max_length=1000)


class UserChange(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    roles: list[str] | None = None
    teams: dict[uuid.UUID, str] | None = None
    active: bool | None = None


class Password(BaseModel):
    password: str = Field(max_length=1000)


@dataclass(frozen=True)
class Caller:
    user: User
    viewer: Viewer
    session_row: UserSession


def _not_found() -> NoReturn:
    raise HTTPException(404, "not found")


def board_router(
    settings: Settings,
    sessions: sessionmaker[Any],
    clock: Clock,
    monotonic: Callable[[], float],
    client_ip: Callable[[Request], str],
) -> APIRouter:
    router = APIRouter(prefix="/api")
    per_ip = RateLimiter(settings.signin_rate_per_minute, monotonic)
    per_email = RateLimiter(settings.signin_rate_per_minute, monotonic)
    secure = settings.env != "local"

    def set_cookie(
        response: Response, name: str, value: str, max_age: int, http_only: bool
    ) -> None:
        response.set_cookie(
            name,
            value,
            max_age=max_age,
            httponly=http_only,
            secure=secure,
            samesite="lax",
            path="/",
        )

    @contextmanager
    def caller(request: Request, writes: bool) -> Iterator[tuple[Session, Caller]]:
        with sessions.begin() as db:
            found = accounts.find_session(db, request.cookies.get(SESSION_COOKIE), clock)
            if found is None:
                raise HTTPException(401, "sign in first")
            row, user = found
            given = request.headers.get(CSRF_HEADER)
            if writes and not accounts.csrf_matches(row.csrf_token, given):
                raise HTTPException(403, "missing or wrong CSRF token")
            yield db, Caller(user, accounts.viewer(db, user), row)

    def user_json(user: User, viewer: Viewer | None = None) -> dict[str, Any]:
        return {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "roles": list(user.roles),
            "active": user.active,
            **({"teams": dict(viewer.teams)} if viewer else {}),
        }

    def team_json(team: Team | None) -> dict[str, Any] | None:
        if team is None:
            return None
        return {"id": str(team.id), "slug": team.slug, "name": team.name}

    def lead_json(db: Session, lead: Lead, viewer: Viewer) -> dict[str, Any]:
        owner = db.get(User, lead.owner_id) if lead.owner_id else None
        team = db.get(Team, lead.team_id) if lead.team_id else None
        return {
            "id": str(lead.id),
            "state": lead.state,
            "team": team_json(team),
            "owner": {"id": str(owner.id), "name": owner.name} if owner else None,
            "name": lead.name,
            "phone": lead.phone,
            "email": lead.email,
            "item": lead.item,
            "arrival_at": lead.arrival_at.isoformat(),
            "pool_since": lead.pool_since.isoformat() if lead.pool_since else None,
            "claimed_at": lead.claimed_at.isoformat() if lead.claimed_at else None,
            "owned_since": lead.owned_since.isoformat() if lead.owned_since else None,
            "claim_minutes": float(lead.claim_minutes) if lead.claim_minutes is not None else None,
            "claim_business_minutes": float(lead.claim_business_minutes)
            if lead.claim_business_minutes is not None
            else None,
            "first_contact_at": iso(lead.first_contact_at),
            "first_contact_minutes": number(lead.first_contact_minutes),
            "first_contact_business_minutes": number(lead.first_contact_business_minutes),
            "uncontacted_since": iso(lead.uncontacted_since),
            "possible_duplicates": list(lead.possible_duplicates),
            "flags": list(lead.flags),
            "actions": leads.allowed_actions(viewer, lead),
            "work": leads.work_actions(viewer, lead),
            **followup_json(db, lead, viewer),
            **leads.urgency(lead, team, clock.now()),
        }

    def followup_json(db: Session, lead: Lead, viewer: Viewer) -> dict[str, Any]:
        """The lead's open follow-up, for those who may read its notes. Overdue means its due
        date, in the team's own time, has passed."""
        if not may_read_notes(viewer, leads.ref(lead)):
            return {}
        found = db.scalar(
            select(Followup).where(Followup.lead_id == lead.id, Followup.state == "open")
        )
        if found is None:
            return {"followup": None}
        team = db.get(Team, lead.team_id) if lead.team_id else None
        try:
            today = clock.now().astimezone(leads.team_hours(team).zone).date() if team else None
        except (ValueError, KeyError, TypeError):
            today = None
        return {
            "followup": {
                "id": str(found.id),
                "due_on": found.due_on.isoformat(),
                "step": found.step,
                "next_step": found.next_step,
                "overdue": today is not None and today > found.due_on,
            }
        }

    def follow_through(db: Session, lead: Lead, viewer: Viewer) -> dict[str, Any]:
        """Notes, contacts, the profile and outcomes, for the lead's team and admins only.
        A monitor gets none of them, not even empty lists."""
        if not may_read_notes(viewer, leads.ref(lead)):
            return {}
        names = dict(db.execute(select(User.id, User.name).where(User.org_id == lead.org_id)).all())
        found_notes = db.scalars(
            select(Note).where(Note.lead_id == lead.id).order_by(Note.created_at, Note.id)
        )
        found_contacts = db.scalars(
            select(Contact).where(Contact.lead_id == lead.id).order_by(Contact.at, Contact.id)
        )
        found_outcomes = db.scalars(
            select(Outcome).where(Outcome.lead_id == lead.id).order_by(Outcome.at, Outcome.id)
        )
        row = notes.profile(db, lead)
        return {
            "notes": [
                {
                    "id": str(n.id),
                    "text": n.text,
                    "author": names.get(n.author_id),
                    "created_at": n.created_at.isoformat(),
                }
                for n in found_notes
            ],
            "contacts": [
                {
                    "id": str(c.id),
                    "note_id": str(c.note_id),
                    "kind": c.kind,
                    "made_by": names.get(c.made_by),
                    "source": c.source,
                    "at": c.at.isoformat(),
                }
                for c in found_contacts
            ],
            "profile": None
            if row is None
            else {
                **{name: getattr(row, name) for name in notes.PROFILE_FIELDS},
                "person_fields": list(row.person_fields),
            },
            "outcomes": [
                {
                    "outcome": o.outcome,
                    "reason": o.reason,
                    "reason_text": o.reason_text,
                    "competitor": o.competitor,
                    "by": names.get(o.by),
                    "at": o.at.isoformat(),
                }
                for o in found_outcomes
            ],
        }

    def message_text(db: Session, message: Message) -> str:
        source = db.get_one(Source, message.source_id)
        try:
            return read(message.raw, source_rules(source), Reference(())).text
        except UnparseableError:
            return ""

    def message_json(db: Session, message: Message, with_text: bool) -> dict[str, Any]:
        source = db.get_one(Source, message.source_id)
        extracted = message.extracted or {}
        return {
            "id": str(message.id),
            "received_at": message.received_at.isoformat(),
            "source_time": message.source_time.isoformat() if message.source_time else None,
            "source": source.slug,
            "status": message.status,
            "reason": message.reason,
            "category": message.category,
            "name": extracted.get("name"),
            "phone": extracted.get("phone"),
            "email": extracted.get("email"),
            "item": extracted.get("item"),
            **({"text": message_text(db, message)} if with_text else {}),
        }

    def lead_action(
        request: Request, act: Callable[[Session, Caller], leads.Done]
    ) -> dict[str, Any]:
        try:
            with caller(request, writes=True) as (db, who):
                done = act(db, who)
                lead = db.get_one(Lead, done.lead_id)
                return {"event": done.event, "lead": lead_json(db, lead, who.viewer)}
        except NotFoundError:
            _not_found()
        except ConflictError as conflict:
            raise HTTPException(409, _conflict_detail(str(conflict))) from conflict
        except InvalidError as invalid:
            raise HTTPException(422, str(invalid)) from invalid

    @router.get("/csrf")
    def csrf(response: Response) -> dict[str, str]:
        """A token for the sign-in form, sent back both as a cookie and in the header."""
        token = secrets.token_urlsafe(32)
        set_cookie(response, CSRF_COOKIE, token, 3600, http_only=False)
        return {"csrf": token}

    @router.post("/session")
    def sign_in(body: SignIn, request: Request, response: Response) -> dict[str, Any]:
        given = request.headers.get(CSRF_HEADER)
        if not accounts.csrf_matches(request.cookies.get(CSRF_COOKIE), given):
            raise HTTPException(403, "missing or wrong CSRF token")
        email = accounts.normal_email(body.email)
        if not per_ip.allow(client_ip(request)) or not per_email.allow(email):
            raise HTTPException(429, "too many sign-in tries; wait a minute")
        with sessions.begin() as db:
            user = accounts.verify(db, email, body.password)
            if user is None:
                raise HTTPException(401, "wrong email or password")
            try:
                started = accounts.start_session(db, user, clock, settings.session_days)
            except accounts.AccountError as changed:
                raise HTTPException(401, "wrong email or password") from changed
            viewer = accounts.viewer(db, user)
        set_cookie(
            response, SESSION_COOKIE, started.token, settings.session_days * 86400, http_only=True
        )
        response.delete_cookie(CSRF_COOKIE, path="/")
        return {"user": user_json(user, viewer), "csrf": started.csrf}

    @router.get("/session")
    def who_am_i(request: Request) -> dict[str, Any]:
        with caller(request, writes=False) as (_, who):
            return {"user": user_json(who.user, who.viewer), "csrf": who.session_row.csrf_token}

    @router.delete("/session", status_code=204)
    def sign_out(request: Request, response: Response) -> None:
        with caller(request, writes=True) as (db, _):
            accounts.end_session(db, request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")

    @router.get("/teams")
    def teams(request: Request) -> list[dict[str, Any]]:
        with caller(request, writes=False) as (db, who):
            query = select(Team).where(Team.org_id == who.user.org_id)
            rows = db.scalars(query.order_by(Team.name))
            return [
                {**(team_json(t) or {}), "my_role": who.viewer.teams.get(str(t.id))} for t in rows
            ]

    @router.get("/teams/{team_id}/members")
    def members(request: Request, team_id: uuid.UUID) -> list[dict[str, Any]]:
        with caller(request, writes=False) as (db, who):
            team = db.get(Team, team_id)
            if team is None or team.org_id != who.user.org_id:
                _not_found()
            if not (who.viewer.admin or who.viewer.leads_team(str(team_id))):
                _not_found()
            rows = db.execute(
                select(User, Membership.role)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.team_id == team_id, User.active)
                .order_by(User.name)
            ).all()
            return [{"id": str(u.id), "name": u.name, "role": role} for u, role in rows]

    @router.get("/leads")
    def list_leads(
        request: Request, view: str = "pool", team_id: uuid.UUID | None = None
    ) -> list[dict[str, Any]]:
        with caller(request, writes=False) as (db, who):
            viewer = who.viewer
            query = select(Lead).where(Lead.org_id == who.user.org_id, Lead.deleted_at.is_(None))
            if view == "mine":
                query = query.where(
                    Lead.owner_id == who.user.id, Lead.state.in_(("owned", "nurture"))
                )
            elif view == "unassigned":
                if not can_see_team(viewer, None):
                    _not_found()
                query = query.where(Lead.team_id.is_(None), Lead.state == "pool")
            elif view in ("pool", "team") and team_id is not None:
                if not can_see_team(viewer, str(team_id)):
                    _not_found()
                query = query.where(Lead.team_id == team_id)
                if view == "pool":
                    query = query.where(Lead.state == "pool")
                elif not (viewer.admin or viewer.monitor or viewer.leads_team(str(team_id))):
                    _not_found()
                else:
                    query = query.where(Lead.state.in_(("owned", "nurture")))
            else:
                raise HTTPException(422, "view is mine, unassigned, or pool or team with a team_id")
            rows = db.scalars(query.order_by(Lead.arrival_at).limit(LIST_LIMIT))
            found = [lead_json(db, lead, viewer) for lead in rows if leads.visible(viewer, lead)]
            if view == "mine":
                # Due follow-ups first, the oldest due date at the top; the rest by arrival.
                found.sort(key=lambda row: (row["followup"] is None, _due(row)))
            return found

    @router.get("/leads/{lead_id}")
    def lead_detail(request: Request, lead_id: uuid.UUID) -> dict[str, Any]:
        with caller(request, writes=False) as (db, who):
            lead = db.get(Lead, lead_id)
            if lead is None or not leads.visible(who.viewer, lead):
                _not_found()
            messages = db.scalars(
                select(Message).where(Message.lead_id == lead.id).order_by(Message.received_at)
            )
            history = list(
                db.scalars(
                    select(LeadEvent).where(LeadEvent.lead_id == lead.id).order_by(LeadEvent.seq)
                )
            )
            return {
                **lead_json(db, lead, who.viewer),
                "messages": [message_json(db, m, with_text=True) for m in messages],
                "events": [
                    {
                        "seq": e.seq,
                        "type": e.type,
                        "at": e.created_at.isoformat(),
                        "payload": e.payload,
                    }
                    for e in history
                ],
                "people": people_named(db, who.user.org_id, history),
                **follow_through(db, lead, who.viewer),
            }

    @router.post("/leads/{lead_id}/claim")
    def claim(request: Request, lead_id: uuid.UUID) -> dict[str, Any]:
        return lead_action(request, lambda db, who: leads.claim(db, who.viewer, lead_id, clock))

    @router.post("/leads/{lead_id}/release")
    def release(request: Request, lead_id: uuid.UUID) -> dict[str, Any]:
        return lead_action(request, lambda db, who: leads.release(db, who.viewer, lead_id, clock))

    @router.post("/leads/{lead_id}/return")
    def return_to_pool(request: Request, lead_id: uuid.UUID) -> dict[str, Any]:
        return lead_action(
            request, lambda db, who: leads.return_to_pool(db, who.viewer, lead_id, clock)
        )

    @router.post("/leads/{lead_id}/reassign")
    def reassign(request: Request, lead_id: uuid.UUID, body: ToUser) -> dict[str, Any]:
        return lead_action(
            request,
            lambda db, who: leads.reassign(db, who.viewer, lead_id, body.user_id, clock),
        )

    @router.post("/leads/{lead_id}/move")
    def move(request: Request, lead_id: uuid.UUID, body: ToTeam) -> dict[str, Any]:
        return lead_action(
            request,
            lambda db, who: leads.move_team(db, who.viewer, lead_id, body.team_id, clock),
        )

    @router.post("/leads/{lead_id}/place")
    def place(request: Request, lead_id: uuid.UUID, body: ToTeam) -> dict[str, Any]:
        return lead_action(
            request, lambda db, who: leads.place(db, who.viewer, lead_id, body.team_id, clock)
        )

    @router.post("/leads/{lead_id}/notes", status_code=201)
    def add_note(request: Request, lead_id: uuid.UUID, body: NewNote) -> dict[str, Any]:
        try:
            with caller(request, writes=True) as (db, who):
                added = notes.add_note(db, who.viewer, lead_id, body.text, body.action, clock)
                lead = db.get_one(Lead, lead_id)
                return {
                    "note_id": str(added.note_id),
                    "contact_id": str(added.contact_id) if added.contact_id else None,
                    "lead": lead_json(db, lead, who.viewer),
                }
        except NotFoundError:
            _not_found()
        except InvalidError as invalid:
            raise HTTPException(422, str(invalid)) from invalid

    @router.put("/leads/{lead_id}/profile")
    def edit_profile(request: Request, lead_id: uuid.UUID, body: ProfileChange) -> dict[str, Any]:
        try:
            with caller(request, writes=True) as (db, who):
                changes = body.model_dump(exclude_unset=True)
                row = notes.edit_profile(db, who.viewer, lead_id, changes, clock)
                return {
                    **{name: getattr(row, name) for name in notes.PROFILE_FIELDS},
                    "person_fields": list(row.person_fields),
                }
        except NotFoundError:
            _not_found()
        except InvalidError as invalid:
            raise HTTPException(422, str(invalid)) from invalid

    @router.post("/leads/{lead_id}/outcome")
    def set_outcome(request: Request, lead_id: uuid.UUID, body: SetOutcome) -> dict[str, Any]:
        return lead_action(
            request,
            lambda db, who: leads.set_outcome(
                db,
                who.viewer,
                lead_id,
                body.outcome,
                body.reason,
                body.reason_text,
                body.competitor,
                clock,
            ),
        )

    @router.post("/leads/{lead_id}/nurture")
    def set_nurture(request: Request, lead_id: uuid.UUID) -> dict[str, Any]:
        return lead_action(
            request, lambda db, who: leads.set_nurture(db, who.viewer, lead_id, clock)
        )

    @router.post("/leads/{lead_id}/resume")
    def resume(request: Request, lead_id: uuid.UUID) -> dict[str, Any]:
        return lead_action(request, lambda db, who: leads.resume(db, who.viewer, lead_id, clock))

    @router.post("/leads/{lead_id}/reopen")
    def reopen(request: Request, lead_id: uuid.UUID) -> dict[str, Any]:
        return lead_action(request, lambda db, who: leads.reopen(db, who.viewer, lead_id, clock))

    @router.delete("/leads/{lead_id}", status_code=204)
    def delete_lead(request: Request, lead_id: uuid.UUID) -> Response:
        try:
            with caller(request, writes=True) as (db, who):
                delete.delete_lead(db, who.viewer, lead_id, clock)
        except NotFoundError:
            _not_found()
        return Response(status_code=204)

    @router.get("/escalations")
    def escalations(request: Request) -> list[dict[str, Any]]:
        """Escalations that fired, newest first, for monitors and admins. `current` says
        whether the lead still sits unclaimed in the same pool stay the level fired for."""
        with caller(request, writes=False) as (db, who):
            if not (who.viewer.monitor or who.viewer.admin):
                _not_found()
            rows = db.execute(
                select(Escalation, Lead)
                .join(Lead, Lead.id == Escalation.lead_id)
                .where(Escalation.org_id == who.user.org_id, Lead.deleted_at.is_(None))
                .order_by(Escalation.fired_at.desc(), Escalation.id)
                .limit(LIST_LIMIT)
            ).all()
            return [
                {
                    "level": fired.level,
                    "fired_at": fired.fired_at.isoformat(),
                    "stay": fired.stay.isoformat(),
                    "current": still_standing(fired, lead),
                    "lead": lead_json(db, lead, who.viewer),
                }
                for fired, lead in rows
            ]

    @router.get("/tray")
    def tray_list(request: Request) -> list[dict[str, Any]]:
        with caller(request, writes=False) as (db, who):
            if not tray.may_see_tray(who.viewer):
                _not_found()
            rows = db.scalars(
                select(Message)
                .where(Message.org_id == who.user.org_id, Message.status == "not_a_lead")
                .order_by(Message.received_at.desc())
                .limit(LIST_LIMIT)
            )
            return [message_json(db, m, with_text=False) for m in rows]

    @router.get("/tray/{message_id}")
    def tray_detail(request: Request, message_id: uuid.UUID) -> dict[str, Any]:
        with caller(request, writes=False) as (db, who):
            message = db.get(Message, message_id)
            if (
                not tray.may_see_tray(who.viewer)
                or message is None
                or message.org_id != who.user.org_id
                or message.status != "not_a_lead"
            ):
                _not_found()
            return message_json(db, message, with_text=True)

    @router.post("/tray/{message_id}/rescue")
    def rescue(request: Request, message_id: uuid.UUID) -> dict[str, Any]:
        try:
            with caller(request, writes=True) as (db, who):
                message = tray.rescue(db, who.viewer, message_id, clock)
                return {"status": message.status, "lead_id": str(message.lead_id)}
        except NotFoundError:
            _not_found()
        except ConflictError as conflict:
            raise HTTPException(409, str(conflict)) from conflict

    @router.get("/inbox")
    def inbox(request: Request) -> list[dict[str, Any]]:
        with caller(request, writes=False) as (db, who):
            rows = db.scalars(
                select(InboxEmail)
                .where(InboxEmail.user_id == who.user.id)
                .order_by(InboxEmail.created_at.desc())
                .limit(LIST_LIMIT)
            )
            return [
                {
                    "id": str(m.id),
                    "subject": m.subject,
                    "text": m.text_body,
                    "html": m.html_body,
                    "links": [
                        {"label": str(link["label"]), "path": link["path"]}
                        for link in m.links or []
                        if IN_APP_PATH.fullmatch(str(link.get("path", "")))
                    ],
                    "created_at": m.created_at.isoformat(),
                    "read": m.read_at is not None,
                }
                for m in rows
            ]

    @router.post("/inbox/{email_id}/read", status_code=204)
    def inbox_read(request: Request, email_id: uuid.UUID) -> None:
        with caller(request, writes=True) as (db, who):
            email = db.get(InboxEmail, email_id)
            if email is None or email.user_id != who.user.id:
                _not_found()
            email.read_at = email.read_at or clock.now()

    @router.get("/push/key")
    def push_key(request: Request) -> dict[str, str | None]:
        with caller(request, writes=False):
            return {"public_key": settings.vapid_public_key or None}

    @router.post("/push/subscriptions", status_code=201)
    def subscribe(request: Request, body: Subscription) -> dict[str, str]:
        if not endpoint_allowed(body.endpoint, settings.push_hosts, settings.push_allow_http):
            raise HTTPException(422, "the endpoint is not on a known push service")
        if not push_keys_valid(body.keys.p256dh, body.keys.auth):
            raise HTTPException(
                422, "the push keys are not a P-256 public key and a 16-byte secret"
            )
        with caller(request, writes=True) as (db, who):
            existing = db.scalar(
                select(PushSubscription).where(PushSubscription.endpoint == body.endpoint)
            )
            if existing is not None and existing.user_id != who.user.id:
                # A browser that signs in as someone else re-subscribes the same endpoint;
                # the device now belongs to the person signed in on it.
                db.delete(existing)
                db.flush()
                existing = None
            if existing is None:
                existing = PushSubscription(
                    org_id=who.user.org_id,
                    user_id=who.user.id,
                    endpoint=body.endpoint,
                    created_at=clock.now(),
                    p256dh=body.keys.p256dh,
                    auth=body.keys.auth,
                )
                db.add(existing)
            existing.p256dh, existing.auth = body.keys.p256dh, body.keys.auth
            db.flush()
            return {"id": str(existing.id)}

    @router.delete("/push/subscriptions", status_code=204)
    def unsubscribe(request: Request, body: Unsubscribe) -> None:
        with caller(request, writes=True) as (db, who):
            existing = db.scalar(
                select(PushSubscription).where(
                    PushSubscription.endpoint == body.endpoint,
                    PushSubscription.user_id == who.user.id,
                )
            )
            if existing is not None:
                db.delete(existing)

    def admin_user(db: Session, who: Caller, user_id: uuid.UUID) -> User:
        if not who.viewer.admin:
            _not_found()
        user = db.get(User, user_id)
        if user is None or user.org_id != who.user.org_id:
            _not_found()
        return user

    def account_error(error: accounts.AccountError) -> HTTPException:
        return HTTPException(422, str(error))

    @router.get("/users")
    def users(request: Request) -> list[dict[str, Any]]:
        with caller(request, writes=False) as (db, who):
            if not who.viewer.admin:
                _not_found()
            query = select(User).where(User.org_id == who.user.org_id)
            rows = db.scalars(query.order_by(User.name))
            return [user_json(u, accounts.viewer(db, u)) for u in rows]

    @router.post("/users", status_code=201)
    def create_user(request: Request, body: NewUser) -> dict[str, Any]:
        try:
            with caller(request, writes=True) as (db, who):
                if not who.viewer.admin:
                    _not_found()
                user = accounts.create_user(
                    db, who.user.org_id, body.email, body.name, body.roles, body.password
                )
                accounts.set_memberships(db, user, body.teams, clock)
                return user_json(user, accounts.viewer(db, user))
        except accounts.AccountError as error:
            raise account_error(error) from error

    @router.patch("/users/{user_id}")
    def change_user(request: Request, user_id: uuid.UUID, body: UserChange) -> dict[str, Any]:
        try:
            with caller(request, writes=True) as (db, who):
                user = admin_user(db, who, user_id)
                if body.name is not None:
                    if not body.name.strip():
                        raise accounts.AccountError("a user needs a name")
                    user.name = body.name.strip()
                if body.roles is not None:
                    accounts.set_roles(user, body.roles)
                if body.teams is not None:
                    accounts.set_memberships(db, user, body.teams, clock)
                if body.active is False and user.active:
                    accounts.deactivate(db, user, clock)
                elif body.active is True:
                    user.active = True
                return user_json(user, accounts.viewer(db, user))
        except accounts.AccountError as error:
            raise account_error(error) from error

    @router.put("/users/{user_id}/password", status_code=204)
    def set_password(request: Request, user_id: uuid.UUID, body: Password) -> None:
        try:
            with caller(request, writes=True) as (db, who):
                user = admin_user(db, who, user_id)
                own = who.session_row.id if user.id == who.user.id else None
                accounts.set_password(db, user, body.password, keep=own)
        except accounts.AccountError as error:
            raise account_error(error) from error

    return router


def _due(row: dict[str, Any]) -> str:
    return row["followup"]["due_on"] if row.get("followup") else ""


def still_standing(fired: Escalation, lead: Lead) -> bool:
    """Whether the lead is still in the stretch the level fired for: unclaimed in the same
    pool stay, or for a contact level, owned with the same uncontacted stretch."""
    if fired.level in escalation.CONTACT_DEFAULTS:
        return lead.state == "owned" and lead.uncontacted_since == fired.stay
    return lead.state == "pool" and lead.pool_since == fired.stay


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _b64url(text: str) -> bytes | None:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, binascii.Error):
        return None


# The only in-app link an inbox email offers: a lead page, by id.
IN_APP_PATH = re.compile(r"/leads/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
PERSON_KEYS = ("by", "owner_id", "from", "to", "rescued_by")


def people_named(db: Session, org_id: uuid.UUID, history: list[LeadEvent]) -> dict[str, str]:
    """Names for the users a lead's history mentions, so the page never has to say "a
    teammate" about a monitor or someone who has left the team."""
    ids: set[uuid.UUID] = set()
    for event in history:
        for key in PERSON_KEYS:
            try:
                ids.add(uuid.UUID(str(event.payload.get(key))))
            except ValueError:
                continue
    if not ids:
        return {}
    rows = db.execute(select(User.id, User.name).where(User.org_id == org_id, User.id.in_(ids)))
    return {str(user_id): name for user_id, name in rows}


def push_keys_valid(p256dh: str, auth: str) -> bool:
    """A browser's subscription keys: an uncompressed P-256 point and a 16-byte secret.
    Checked here so a bad pair never reaches the push library."""
    point, secret = _b64url(p256dh), _b64url(auth)
    if point is None or len(point) != 65 or secret is None or len(secret) != 16:
        return False
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    except ValueError:
        return False
    return True


def _conflict_detail(reason: str) -> str:
    if reason.startswith("claim from "):
        return "already claimed"
    return "the lead is not in a state that allows this"
