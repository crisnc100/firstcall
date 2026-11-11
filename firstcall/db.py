"""Database tables and the session factory.

Every domain table carries a non-null org_id, so isolation per company can come later.
`orgs` is the root of that tree, and `heartbeats` records processes, not company data.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

NOT_DOMAIN_TABLES = frozenset({"orgs", "heartbeats"})

WORKING_STATUSES = ("received", "processing")
TERMINAL_STATUSES = (
    "lead_created",
    "attached",
    "not_a_lead",
    "duplicate_content",
    "failed",
)
LEAD_STATES = ("pool", "owned", "nurture", "won", "lost", "not_real")
# duplicate_delivery is a terminal answer, not a row: a repeat delivery stores nothing new.
MESSAGE_STATUSES = WORKING_STATUSES + TERMINAL_STATUSES


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON, list[Any]: JSON}


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


def _org() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("orgs.id"), nullable=False, index=True)


class Org(Base):
    __tablename__ = "orgs"
    id: Mapped[uuid.UUID] = _uuid()
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("org_id", "slug"),)
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64))
    business_hours: Mapped[dict[str, Any]] = mapped_column(JSON)
    # Items of interest (listings, products) that place a message into this team.
    items: Mapped[list[Any]] = mapped_column(JSON, default=list)


class Agent(Base):
    """A third party who writes on a buyer's behalf. Their contacts are never identities."""

    __tablename__ = "agents"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(32))


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(_in("kind", ("email", "form", "call")), name="source_kind"),
        CheckConstraint(_in("auth_method", ("basic", "token", "hmac")), name="source_auth"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    auth_method: Mapped[str] = mapped_column(String(16))
    basic_user: Mapped[str | None] = mapped_column(String(200))
    basic_password_hash: Mapped[str | None] = mapped_column(Text)
    token_hash: Mapped[str | None] = mapped_column(String(64))
    # The HMAC secret lives in this environment variable, never in the database.
    hmac_secret_env: Mapped[str | None] = mapped_column(String(100))
    field_mapping: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    default_team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"))
    delivery_id_header: Mapped[str | None] = mapped_column(String(100))
    relay_senders: Mapped[list[Any]] = mapped_column(JSON, default=list)
    tracking_numbers: Mapped[list[Any]] = mapped_column(JSON, default=list)
    spam_senders: Mapped[list[Any]] = mapped_column(JSON, default=list)
    honeypot_field: Mapped[str | None] = mapped_column(String(100))


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("source_id", "delivery_key"),
        CheckConstraint(_in("status", MESSAGE_STATUSES), name="message_status"),
        CheckConstraint(
            f"{_in('status', WORKING_STATUSES)} OR (reason IS NOT NULL AND reason <> '')",
            name="terminal_has_reason",
        ),
        Index("messages_source_body", "source_id", "body_sha256", "received_at"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"))
    # Arrival time: the database clock at insert. Every timer starts here.
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    source_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_type: Mapped[str] = mapped_column(String(100))
    raw: Mapped[bytes] = mapped_column(LargeBinary)
    body_sha256: Mapped[str] = mapped_column(String(64))
    delivery_key: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(32), default="received")
    reason: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(16))
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.id"), index=True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"))
    clean_body_sha256: Mapped[str | None] = mapped_column(String(64))
    # Set when the model read was skipped; the PR1b reread job fills the gaps.
    needs_reread: Mapped[bool] = mapped_column(Boolean, default=False)


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (CheckConstraint(_in("state", LEAD_STATES), name="lead_state"),)
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    # None is the Unassigned pool.
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"), index=True)
    state: Mapped[str] = mapped_column(String(16), default="pool")
    created_by_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", use_alter=True), unique=True
    )
    arrival_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(320))
    item: Mapped[str | None] = mapped_column(String(200))
    # Lead ids this lead may duplicate; a person decides, nothing merges automatically.
    possible_duplicates: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Identity(Base):
    """A normalised phone or email that later messages from the same buyer match on."""

    __tablename__ = "identities"
    __table_args__ = (
        UniqueConstraint("lead_id", "kind", "value"),
        CheckConstraint(_in("kind", ("phone", "email")), name="identity_kind"),
        Index("identities_lookup", "org_id", "kind", "value"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"))
    kind: Mapped[str] = mapped_column(String(8))
    value: Mapped[str] = mapped_column(String(320))


class Activity(Base):
    """A later message added to an existing lead."""

    __tablename__ = "activities"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), index=True)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LeadEvent(Base):
    """Append-only; written in the same transaction as the change it records (the outbox)."""

    __tablename__ = "lead_events"
    __table_args__ = (UniqueConstraint("lead_id", "seq"), UniqueConstraint("lead_id", "cause"))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    org_id: Mapped[uuid.UUID] = _org()
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"))
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    # What caused the event, such as "message:<id>"; replaying a cause adds nothing.
    cause: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("message_id", "kind"),
        CheckConstraint(_in("state", ("queued", "running", "done", "failed")), name="job_state"),
        Index("jobs_ready", "state", "run_after"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"))
    kind: Mapped[str] = mapped_column(String(32), default="process")
    state: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class ModelCall(Base):
    """One model call with its cost. Written from PR1b; the table exists from the start."""

    __tablename__ = "model_calls"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id"))
    model: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(16))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BudgetLedger(Base):
    """Model spend per org and day, so the daily cap survives a restart."""

    __tablename__ = "budget_ledger"
    __table_args__ = (UniqueConstraint("org_id", "day"),)
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    day: Mapped[date] = mapped_column(Date)
    spent_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    calls: Mapped[int] = mapped_column(BigInteger, default=0)


class Heartbeat(Base):
    """A worker or scheduler's last sign of life, read by its compose healthcheck."""

    __tablename__ = "heartbeats"
    process: Mapped[str] = mapped_column(String(200), primary_key=True)
    beat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def make_sessions(engine: Engine) -> sessionmaker[Any]:
    return sessionmaker(engine, expire_on_commit=False)
