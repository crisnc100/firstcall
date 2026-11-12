"""Users, memberships, passwords and signed-in sessions.

Passwords are argon2id hashes. A session cookie holds a random token; the database keeps
only its SHA-256, so a leaked table gives no working cookie. Each session has its own CSRF
token, which the page sends back in a header on every request that changes state.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from firstcall.board import leads
from firstcall.core.access import Viewer
from firstcall.core.clock import Clock
from firstcall.core.identity import bare_address
from firstcall.db import ORG_ROLES, TEAM_ROLES, Membership, Team, User, UserSession

MIN_PASSWORD = 12
_hasher = PasswordHasher()
# Each argon2 check costs tens of milliseconds of CPU, so only a few run at once.
_verifying = threading.BoundedSemaphore(4)


class AccountError(ValueError):
    pass


@cache
def _decoy_hash() -> str:
    """Checked against when no such user exists, so a wrong email takes as long as a wrong
    password and sign-in timing does not tell which emails have accounts."""
    return _hasher.hash(secrets.token_urlsafe(16))


def normal_email(email: str) -> str:
    return email.strip().lower()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _roles(roles: list[str]) -> list[str]:
    unknown = set(roles) - set(ORG_ROLES)
    if unknown:
        raise AccountError(f"unknown roles: {', '.join(sorted(unknown))}")
    return sorted(set(roles))


def create_user(
    session: Session,
    org_id: uuid.UUID,
    email: str,
    name: str,
    roles: list[str] | None = None,
    password: str | None = None,
) -> User:
    address = bare_address(email)
    if address is None:
        raise AccountError("a user's email must be one plain address, like name@example.com")
    if not name.strip():
        raise AccountError("a user needs a name")
    taken = session.scalar(select(User.id).where(User.org_id == org_id, User.email == address))
    if taken is not None:
        raise AccountError("a user with that email already exists")
    user = User(
        id=uuid.uuid4(),
        org_id=org_id,
        email=address,
        name=name.strip(),
        roles=_roles(roles or []),
        active=True,
    )
    if password is not None:
        _check_password(password)
        user.password_hash = _hasher.hash(password)
    session.add(user)
    session.flush()
    return user


def set_password(
    session: Session, user: User, password: str, keep: uuid.UUID | None = None
) -> None:
    """Set the password and end every session of the user's but `keep`, the one making the
    change when users change their own, so a leaked cookie stops working too. The user row
    is locked first, so a sign-in that checked the old password either commits its session
    before this (and the delete below ends it) or waits and finds the hash changed."""
    _check_password(password)
    leads.lock_user(session, user.id, share=False)
    user.password_hash = _hasher.hash(password)
    ended = delete(UserSession).where(UserSession.user_id == user.id)
    if keep is not None:
        ended = ended.where(UserSession.id != keep)
    session.execute(ended)


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AccountError(f"a password needs at least {MIN_PASSWORD} characters")


def set_roles(user: User, roles: list[str]) -> None:
    user.roles = _roles(roles)


def set_memberships(
    session: Session, user: User, wanted: Mapping[uuid.UUID, str], clock: Clock
) -> None:
    """Make the user's teams exactly `wanted` (team id to role). Leaving a team returns the
    user's open leads there to that team's pool."""
    if set(wanted.values()) - set(TEAM_ROLES):
        raise AccountError("a team role is salesperson or team_lead")
    teams = set(session.scalars(select(Team.id).where(Team.org_id == user.org_id)))
    if set(wanted) - teams:
        raise AccountError("no such team")
    leads.lock_user(session, user.id, share=False)
    rows = session.scalars(select(Membership).where(Membership.user_id == user.id))
    current = {m.team_id: m for m in rows}
    for team_id, membership in current.items():
        if team_id not in wanted:
            session.delete(membership)
            leads.owner_left(session, user.id, clock, team_id)
    for team_id, role in wanted.items():
        if team_id in current:
            current[team_id].role = role
        else:
            session.add(Membership(org_id=user.org_id, user_id=user.id, team_id=team_id, role=role))
    session.flush()


def deactivate(session: Session, user: User, clock: Clock) -> list[leads.Done]:
    """The user can no longer sign in, every session ends, and their open leads go back to
    their pools."""
    leads.lock_user(session, user.id, share=False)
    user.active = False
    session.flush()
    session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    return leads.owner_left(session, user.id, clock)


def verify(session: Session, email: str, password: str) -> User | None:
    """The active user with this email and password, or None."""
    users = list(session.scalars(select(User).where(User.email == normal_email(email))))
    user = users[0] if len(users) == 1 else None
    stored = user.password_hash if user and user.password_hash else _decoy_hash()
    with _verifying:
        try:
            ok = _hasher.verify(stored, password)
        except (Argon2Error, InvalidHashError):
            ok = False
    if not ok or user is None or not user.password_hash or not user.active:
        return None
    return user


@dataclass(frozen=True)
class Started:
    token: str
    csrf: str
    expires_at: datetime


def start_session(session: Session, user: User, clock: Clock, days: int) -> Started:
    """A session for a user whose password was just checked. Under a share lock on the
    user row, the stored hash must still be the one checked and the user still active:
    a password reset or a deactivation that landed during the check wins."""
    checked = user.password_hash
    current = session.execute(
        select(User.password_hash, User.active)
        .where(User.id == user.id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if current is None or current.password_hash != checked or not current.active:
        raise AccountError("the password or the account changed during sign-in")
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = clock.now()
    expires = now + timedelta(days=days)
    session.add(
        UserSession(
            org_id=user.org_id,
            user_id=user.id,
            token_hash=hash_token(token),
            csrf_token=csrf,
            created_at=now,
            expires_at=expires,
        )
    )
    return Started(token, csrf, expires)


def find_session(
    session: Session, token: str | None, clock: Clock
) -> tuple[UserSession, User] | None:
    if not token:
        return None
    row = session.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == hash_token(token))
    ).first()
    if row is None:
        return None
    found, user = row
    if found.expires_at <= clock.now() or not user.active:
        return None
    return found, user


def end_session(session: Session, token: str | None) -> None:
    if token:
        session.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))


def csrf_matches(expected: str | None, given: str | None) -> bool:
    return bool(expected and given) and hmac.compare_digest(
        (expected or "").encode(), (given or "").encode()
    )


def viewer(session: Session, user: User) -> Viewer:
    teams = {
        str(m.team_id): m.role
        for m in session.scalars(select(Membership).where(Membership.user_id == user.id))
    }
    return Viewer(str(user.id), str(user.org_id), frozenset(user.roles), teams)
