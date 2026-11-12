"""Process one stored message: rules read, place, match, category gate, then create a lead or
attach to one, all in the caller's transaction.

Every effect has a unique key (a lead on the message that created it, an activity on its
message, a lead event on its cause), so processing a message twice changes nothing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from firstcall import events, failpoints
from firstcall.board import alerts
from firstcall.core import delivery
from firstcall.core.clock import Clock
from firstcall.core.identity import first_name
from firstcall.core.match import Candidate, decide, gate
from firstcall.core.model_read import combine
from firstcall.core.place import place, team_key
from firstcall.core.read import AgentRef, Reading, Reference, SourceRules, UnparseableError, read
from firstcall.db import (
    TERMINAL_STATUSES,
    Activity,
    Agent,
    Identity,
    Lead,
    Message,
    Source,
    Team,
)
from firstcall.locks import lock_all
from firstcall.pipeline.model_read import ModelReader

FAILPOINT_AFTER_LEAD_INSERT = "after_lead_insert"


@dataclass(frozen=True)
class OrgView:
    reference: Reference
    item_teams: dict[str, str]
    team_names: dict[str, str]
    team_ids: dict[str, uuid.UUID]
    team_slugs: dict[uuid.UUID, str]
    # Each team's slug and display name, the team list the model chooses from.
    team_labels: dict[str, str]
    never_phones: frozenset[str]
    never_emails: frozenset[str]


def org_view(session: Session, org_id: uuid.UUID, source: Source) -> OrgView:
    teams = list(session.scalars(select(Team).where(Team.org_id == org_id)))
    agents = list(session.scalars(select(Agent).where(Agent.org_id == org_id)))
    items = {str(item): team.slug for team in teams for item in team.items}
    names: dict[str, str] = {}
    for team in teams:
        names[team_key(team.slug)] = team.slug
        names[team_key(team.name)] = team.slug
    refs = tuple(AgentRef(a.name, a.email, a.phone) for a in agents)
    return OrgView(
        reference=Reference(tuple(sorted(items)), refs),
        item_teams=items,
        team_names=names,
        team_ids={t.slug: t.id for t in teams},
        team_slugs={t.id: t.slug for t in teams},
        team_labels={t.slug: t.name for t in teams},
        never_phones=frozenset(
            {a.phone for a in agents if a.phone} | {str(n) for n in source.tracking_numbers}
        ),
        never_emails=frozenset(
            {a.email.lower() for a in agents if a.email}
            | {str(s).lower() for s in source.relay_senders}
        ),
    )


def source_rules(source: Source) -> SourceRules:
    return SourceRules(
        kind=source.kind,
        field_mapping={str(k): str(v) for k, v in source.field_mapping.items()},
        relay_senders=frozenset(str(s) for s in source.relay_senders),
        tracking_numbers=frozenset(str(s) for s in source.tracking_numbers),
        spam_senders=frozenset(str(s) for s in source.spam_senders),
        honeypot_field=source.honeypot_field,
    )


def identities(reading: Reading, view: OrgView) -> list[tuple[str, str]]:
    """The message's identity keys. Agent, relay and tracking contacts never count."""
    keys: list[tuple[str, str]] = []
    agent = reading.agent
    agent_phone = agent.phone if agent else None
    agent_email = agent.email.lower() if agent and agent.email else None
    if reading.phone and reading.phone not in view.never_phones and reading.phone != agent_phone:
        keys.append(("phone", reading.phone))
    if reading.email and reading.email not in view.never_emails and reading.email != agent_email:
        keys.append(("email", reading.email))
    return keys


def _candidates(
    session: Session, org_id: uuid.UUID, keys: list[tuple[str, str]], view: OrgView
) -> list[Candidate]:
    if not keys:
        return []
    clauses = [and_(Identity.kind == kind, Identity.value == value) for kind, value in keys]
    rows = session.execute(
        select(Identity.lead_id, Identity.kind, Lead.team_id, Lead.name)
        .join(Lead, Lead.id == Identity.lead_id)
        .where(Identity.org_id == org_id, or_(*clauses))
    ).all()
    found: dict[uuid.UUID, dict[str, Any]] = {}
    for lead_id, kind, team_id, name in rows:
        entry = found.setdefault(
            lead_id,
            {
                "team": view.team_slugs.get(team_id) if team_id else None,
                "name": name,
                "kinds": set(),
            },
        )
        entry["kinds"].add(kind)
    return [
        Candidate(
            lead_id=str(lead_id),
            team=entry["team"],
            first_name=first_name(entry["name"]),
            by_email="email" in entry["kinds"],
            by_phone="phone" in entry["kinds"],
        )
        for lead_id, entry in sorted(found.items(), key=lambda kv: str(kv[0]))
    ]


def _add_identities(session: Session, lead: Lead, keys: list[tuple[str, str]]) -> None:
    rows = session.execute(select(Identity.kind, Identity.value).where(Identity.lead_id == lead.id))
    existing = {(kind, value) for kind, value in rows}
    for kind, value in keys:
        if (kind, value) not in existing:
            session.add(Identity(org_id=lead.org_id, lead_id=lead.id, kind=kind, value=value))


def _is_reforward(session: Session, message: Message, lead_id: uuid.UUID) -> bool:
    # Within the window on either side: after a backlog, the later copy can be processed first.
    match = session.scalar(
        select(Message.id)
        .where(
            Message.lead_id == lead_id,
            Message.id != message.id,
            Message.clean_body_sha256 == message.clean_body_sha256,
            Message.received_at > message.received_at - delivery.REFORWARD_WINDOW,
            Message.received_at < message.received_at + delivery.REFORWARD_WINDOW,
        )
        .limit(1)
    )
    return match is not None


def _model_pass(
    message: Message, source: Source, reading: Reading, view: OrgView, reader: ModelReader | None
) -> Reading:
    """The reading after the model read, and the message's reread mark. With no answer the
    rules read stands and the category fails open: the rules call only spam, so anything
    else is a lead until the reread."""
    if reader is None:
        message.needs_reread = True
        return reading
    if reading.spam_signals and reader.methods["category"] != "model":
        # A honeypot or listed spam sender already decides the category; a model call would
        # only spend budget on a spam burst.
        message.needs_reread = False
        return reading
    outcome = reader.read(message, source.kind, reading, view.reference.items, view.team_labels)
    if outcome.answer is None:
        message.needs_reread = True
        message.model_unavailable = outcome.reason
        return reading
    message.needs_reread = False
    message.model_unavailable = None
    message.model_answer = outcome.answer.as_json()
    return combine(reading, outcome.answer, reader.methods)


def process(
    session: Session, message_id: uuid.UUID, clock: Clock, reader: ModelReader | None = None
) -> str:
    """The message's terminal status. A message already terminal is left as it is. Without a
    reader every message takes the rules path and is marked for a reread."""
    # FOR NO KEY UPDATE, not FOR UPDATE: the gateway logs each model call in its own
    # transaction, and that row's foreign key to this message must not wait on this lock.
    message = session.scalar(
        select(Message).where(Message.id == message_id).with_for_update(key_share=True)
    )
    if message is None:
        raise LookupError(f"no message {message_id}")
    if message.status in TERMINAL_STATUSES:
        return message.status
    source = session.get_one(Source, message.source_id)
    view = org_view(session, message.org_id, source)
    message.status = "processing"
    try:
        reading = read(message.raw, source_rules(source), view.reference)
    except UnparseableError as error:
        message.status, message.reason = "failed", f"unparseable: {error}"
        return message.status
    reading = _model_pass(message, source, reading, view, reader)
    message.category = reading.category
    message.extracted = reading.as_json()
    message.source_time = reading.source_time
    if source.kind == "email":
        message.clean_body_sha256 = delivery.clean_body_sha256(reading.text)
    return settle(session, message, source, view, reading, clock)


def settle(
    session: Session,
    message: Message,
    source: Source,
    view: OrgView,
    reading: Reading,
    clock: Clock,
    rescued_by: uuid.UUID | None = None,
) -> str:
    """Place, match, gate, then create or attach, write the event and queue the alerts. A
    rescue passes the monitor who moved the message out of the Not-a-lead tray."""
    default_team = view.team_slugs.get(source.default_team_id) if source.default_team_id else None
    placed = place(reading.item, reading.team_hint, default_team, view.item_teams, view.team_names)
    keys = identities(reading, view)
    lock_all(session, [("identity", str(message.org_id), kind, value) for kind, value in keys])
    decision = decide(
        placed, first_name(reading.name), _candidates(session, message.org_id, keys, view)
    )
    outcome = gate(decision, reading.category)
    cause = f"message:{message.id}"
    now = clock.now()
    rescue = {"rescued_by": str(rescued_by)} if rescued_by else {}

    if outcome == "not_a_lead":
        signals = ", ".join(reading.spam_signals) or "no signal"
        message.status, message.reason = "not_a_lead", f"{reading.category} ({signals})"
        return message.status

    if outcome == "attach":
        assert decision.attach_to is not None
        lead = session.get_one(Lead, uuid.UUID(decision.attach_to), with_for_update=True)
        message.lead_id = lead.id
        message.team_id = lead.team_id
        # A message that waited in a retry can attach after a later one made the lead; the
        # lead's timers start at the buyer's earliest message either way.
        if message.received_at < lead.arrival_at:
            lead.arrival_at = message.received_at
        if source.kind == "email" and _is_reforward(session, message, lead.id):
            message.status, message.reason = (
                "duplicate_content",
                "re-forward of a message on this lead",
            )
            return message.status
        session.add(Activity(org_id=lead.org_id, lead_id=lead.id, message_id=message.id))
        _add_identities(session, lead, keys)
        payload = {"message_id": str(message.id), **rescue}
        events.write(session, lead, "lead.activity", cause, payload, now)
        alerts.activity(session, lead, cause, now)
        message.status, message.reason = "attached", "matched a known buyer"
        return message.status

    team_id = view.team_ids.get(decision.team) if decision.team else None
    lead = Lead(
        id=uuid.uuid4(),
        org_id=message.org_id,
        team_id=team_id,
        state="pool",
        created_by_message_id=message.id,
        arrival_at=message.received_at,
        pool_since=message.received_at,
        name=reading.name,
        phone=reading.phone if ("phone", reading.phone) in keys else None,
        email=reading.email if ("email", reading.email) in keys else None,
        item=reading.item,
        possible_duplicates=list(decision.possible_duplicates),
    )
    session.add(lead)
    session.flush()
    failpoints.fire(FAILPOINT_AFTER_LEAD_INSERT)
    _add_identities(session, lead, keys)
    payload = {
        "message_id": str(message.id),
        "team": decision.team,
        "possible_duplicates": list(decision.possible_duplicates),
        **rescue,
    }
    events.write(session, lead, "lead.created", cause, payload, now)
    alerts.entered_pool(session, lead, "new_lead", cause, now)
    message.lead_id = lead.id
    message.team_id = team_id
    message.status = "lead_created"
    message.reason = "new lead, possible duplicate" if decision.possible_duplicates else "new lead"
    if rescued_by:
        message.reason = "rescued from the Not-a-lead tray"
    return message.status
