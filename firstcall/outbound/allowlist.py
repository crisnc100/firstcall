"""Who Firstcall may send to. Buyers are never on the list, whatever a user record says."""

from __future__ import annotations

import logging
import re
import uuid
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from firstcall.core.identity import normalize_phone
from firstcall.db import Identity, Lead, Message, PushSubscription, User

log = logging.getLogger("firstcall.outbound")


class RefusedError(Exception):
    pass


def _refuse(reason: str) -> RefusedError:
    log.warning("outbound send refused: %s", reason)
    return RefusedError(reason)


def _buyer_emails(session: Session, org_id: uuid.UUID, address: str) -> bool:
    by_identity = select(Identity.id).where(
        Identity.org_id == org_id, Identity.kind == "email", Identity.value == address
    )
    by_lead = select(Lead.id).where(Lead.org_id == org_id, Lead.email == address)
    # Messages in the Not-a-lead tray have no lead or identity, but their sender may still be
    # a buyer, so their read email counts too.
    by_message = select(Message.id).where(
        Message.org_id == org_id, Message.extracted["email"].as_string() == address
    )
    return any(session.scalar(q.limit(1)) is not None for q in (by_identity, by_lead, by_message))


def _buyer_phone(session: Session, org_id: uuid.UUID, phone: str) -> bool:
    found = session.scalar(
        select(Identity.id)
        .where(Identity.org_id == org_id, Identity.kind == "phone", Identity.value == phone)
        .limit(1)
    )
    if found is not None:
        return True
    lead = select(Lead.id).where(Lead.org_id == org_id, Lead.phone == phone).limit(1)
    message = (
        select(Message.id)
        .where(Message.org_id == org_id, Message.extracted["phone"].as_string() == phone)
        .limit(1)
    )
    return session.scalar(lead) is not None or session.scalar(message) is not None


def check_email(session: Session, org_id: uuid.UUID, address: str) -> User:
    """The active user this address belongs to. Refuses a buyer's address, and an address
    whose local part is a buyer's phone, which is how email-to-text gateways are addressed."""
    normal = address.strip().lower()
    if not normal or re.search(r"[\x00-\x1f\x7f]", normal):
        raise _refuse("the address has control characters or is empty")
    if _buyer_emails(session, org_id, normal):
        raise _refuse("the address belongs to a buyer")
    phone = normalize_phone(normal.split("@", 1)[0])
    if phone and _buyer_phone(session, org_id, phone):
        raise _refuse("the address carries a buyer's phone")
    user = session.scalar(
        select(User).where(User.org_id == org_id, User.email == normal, User.active)
    )
    if user is None:
        raise _refuse("the address is not an active user of the org")
    return user


def check_push(
    session: Session, subscription: PushSubscription, user_id: uuid.UUID, allow_http: bool
) -> None:
    """The device belongs to the alert's user, who is active, and the endpoint is HTTPS."""
    owner = session.get(User, subscription.user_id)
    if owner is None or owner.id != user_id or not owner.active:
        raise _refuse("the device does not belong to an active recipient")
    if owner.org_id != subscription.org_id:
        raise _refuse("the device belongs to another org")
    scheme = urlsplit(subscription.endpoint).scheme
    if scheme != "https" and not (allow_http and scheme == "http"):
        raise _refuse("push endpoints must be https")
