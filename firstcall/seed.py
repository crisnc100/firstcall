"""Load the demo company from config/demo.yaml. Running it again changes nothing."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.corpus.world import World, load_world
from firstcall.db import Agent, Org, Source, Team
from firstcall.intake import auth

SOURCE_FIELDS = (
    "kind",
    "auth_method",
    "basic_user",
    "hmac_secret_env",
    "delivery_id_header",
    "honeypot_field",
)
SOURCE_LISTS = ("relay_senders", "tracking_numbers", "spam_senders", "allowed_origins")


def seed(sessions: sessionmaker[Any], world: World | None = None) -> uuid.UUID:
    world = world or load_world()
    raw = world.raw
    with sessions.begin() as session:
        org = session.scalar(select(Org).where(Org.slug == raw["org"]["slug"]))
        if org is None:
            org = Org(id=uuid.uuid4(), slug=raw["org"]["slug"], name=raw["org"]["name"])
            session.add(org)
            session.flush()
        teams = _teams(session, org, raw["teams"])
        _agents(session, org, raw["agents"])
        for spec in raw["sources"]:
            _source(session, org, spec, teams)
        return org.id


def _teams(session: Session, org: Org, specs: list[dict[str, Any]]) -> dict[str, uuid.UUID]:
    existing = {t.slug: t for t in session.scalars(select(Team).where(Team.org_id == org.id))}
    for spec in specs:
        team = existing.get(spec["slug"]) or Team(id=uuid.uuid4(), org_id=org.id, slug=spec["slug"])
        team.name = spec["name"]
        team.timezone = spec["timezone"]
        team.business_hours = dict(spec["business_hours"])
        team.items = list(spec["items"])
        session.add(team)
        existing[spec["slug"]] = team
    session.flush()
    return {slug: team.id for slug, team in existing.items()}


def _agents(session: Session, org: Org, specs: list[dict[str, Any]]) -> None:
    known = {
        (a.name, a.email, a.phone)
        for a in session.scalars(select(Agent).where(Agent.org_id == org.id))
    }
    for spec in specs:
        key = (spec["name"], spec.get("email"), spec.get("phone"))
        if key not in known:
            session.add(Agent(org_id=org.id, name=key[0], email=key[1], phone=key[2]))


def _source(session: Session, org: Org, spec: dict[str, Any], teams: dict[str, uuid.UUID]) -> None:
    source = session.scalar(select(Source).where(Source.slug == spec["slug"]))
    if source is None:
        source = Source(id=uuid.uuid4(), org_id=org.id, slug=spec["slug"])
    for name in SOURCE_FIELDS:
        setattr(source, name, spec.get(name))
    for name in SOURCE_LISTS:
        setattr(source, name, list(spec.get(name, [])))
    source.field_mapping = dict(spec.get("field_mapping", {}))
    default_team = spec.get("default_team")
    source.default_team_id = teams[default_team] if default_team else None
    session.add(source)


def set_secret(sessions: sessionmaker[Any], slug: str) -> str:
    """A new token or password for a source, returned once and stored only as a hash. The
    old one stops working at once."""
    with sessions.begin() as session:
        source = session.scalar(select(Source).where(Source.slug == slug))
        if source is None:
            raise LookupError(f"no source named {slug}")
        secret = auth.new_token()
        if source.auth_method == "token":
            source.token_hash = auth.hash_token(secret)
        elif source.auth_method == "basic":
            source.basic_password_hash = auth.hash_password(secret)
        else:
            raise ValueError(f"{slug} signs with HMAC; set {source.hmac_secret_env} instead")
        return secret
