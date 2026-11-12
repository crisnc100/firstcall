"""The board's JSON API: sign-in, pools, lead actions, the tray, alerts and users.

Every route but sign-in needs a session cookie, and every route that changes state needs
the session's CSRF token in the X-CSRF-Token header. A lead, message or user the caller may
not see or act on answers 404, exactly as if it did not exist.
"""

from __future__ import annotations

import base64
import binascii
import logging
import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, NoReturn

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts, leads, tray
from firstcall.board.leads import ConflictError, InvalidError, NotFoundError
from firstcall.core.access import Viewer, can_see_team
from firstcall.core.clock import Clock
from firstcall.core.read import Reference, UnparseableError, read
from firstcall.db import (
    Escalation,
    InboxEmail,
    Lead,
    LeadEvent,
    Membership,
    Message,
    PushSubscription,
    Source,
    Team,
    User,
    UserSession,
)
from firstcall.intake.limits import RateLimiter
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
            "claim_minutes": float(lead.claim_minutes) if lead.claim_minutes is not None else None,
            "claim_business_minutes": float(lead.claim_business_minutes)
            if lead.claim_business_minutes is not None
            else None,
            "possible_duplicates": list(lead.possible_duplicates),
            "flags": list(lead.flags),
            "actions": leads.allowed_actions(viewer, lead),
            **leads.urgency(lead, team, clock.now()),
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

    # --- sign-in ----------------------------------------------------------------------

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

    # --- teams and pools --------------------------------------------------------------

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
            query = select(Lead).where(Lead.org_id == who.user.org_id)
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
            return [lead_json(db, lead, viewer) for lead in rows if leads.visible(viewer, lead)]

    @router.get("/leads/{lead_id}")
    def lead_detail(request: Request, lead_id: uuid.UUID) -> dict[str, Any]:
        with caller(request, writes=False) as (db, who):
            lead = db.get(Lead, lead_id)
            if lead is None or not leads.visible(who.viewer, lead):
                _not_found()
            messages = db.scalars(
                select(Message).where(Message.lead_id == lead.id).order_by(Message.received_at)
            )
            history = db.scalars(
                select(LeadEvent).where(LeadEvent.lead_id == lead.id).order_by(LeadEvent.seq)
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
                .where(Escalation.org_id == who.user.org_id)
                .order_by(Escalation.fired_at.desc(), Escalation.id)
                .limit(LIST_LIMIT)
            ).all()
            return [
                {
                    "level": fired.level,
                    "fired_at": fired.fired_at.isoformat(),
                    "stay": fired.stay.isoformat(),
                    "current": lead.state == "pool" and lead.pool_since == fired.stay,
                    "lead": lead_json(db, lead, who.viewer),
                }
                for fired, lead in rows
            ]

    # --- the Not-a-lead tray ----------------------------------------------------------

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

    # --- alerts: the in-app inbox and push devices ------------------------------------

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
        if not body.endpoint.startswith("https://") and not (
            settings.push_allow_http and body.endpoint.startswith("http://")
        ):
            raise HTTPException(422, "a push endpoint must be https")
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

    # --- users (admin) ----------------------------------------------------------------

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


def _b64url(text: str) -> bytes | None:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, binascii.Error):
        return None


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
