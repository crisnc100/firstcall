"""The worker and scheduler processes, and the heartbeats their healthchecks read."""

from __future__ import annotations

import socket
import sys
import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

from firstcall.core.clock import Clock
from firstcall.db import Heartbeat
from firstcall.pipeline.jobs import reap, run_one
from firstcall.settings import Settings

HEARTBEAT_SECONDS = 5.0
TICK_SECONDS = 30.0
HEALTHY_WITHIN_SECONDS = 60


def process_name(kind: str) -> str:
    return f"{kind}:{socket.gethostname()}"


def beat(sessions: sessionmaker[Any], kind: str) -> None:
    statement = insert(Heartbeat).values(process=process_name(kind), beat_at=func.now())
    with sessions.begin() as session:
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[Heartbeat.process], set_={"beat_at": func.now()}
            )
        )


def healthy(sessions: sessionmaker[Any], kind: str) -> bool:
    with sessions() as session:
        age = session.scalar(
            select(func.extract("epoch", func.now() - Heartbeat.beat_at)).where(
                Heartbeat.process == process_name(kind)
            )
        )
    return age is not None and float(age) < HEALTHY_WITHIN_SECONDS


def tick(sessions: sessionmaker[Any], clock: Clock, settings: Settings) -> dict[str, int]:
    """One scheduler pass. Escalations and follow-ups join the reaper in later versions."""
    return {"reaped": reap(sessions, clock, settings.max_attempts)}


def work(
    sessions: sessionmaker[Any], clock: Clock, settings: Settings, until_idle: bool = False
) -> int:
    """Process jobs until stopped, or until the queue is empty when until_idle is set.
    Returns how many jobs ran."""
    beat(sessions, "worker")
    print("firstcall worker ready", file=sys.stderr, flush=True)
    last_beat = time.monotonic()
    ran = 0
    while True:
        if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
            beat(sessions, "worker")
            last_beat = time.monotonic()
        if run_one(sessions, clock, settings.job_lease_seconds, settings.max_attempts):
            ran += 1
            continue
        if until_idle:
            return ran
        time.sleep(0.5)


def schedule(sessions: sessionmaker[Any], clock: Clock, settings: Settings) -> None:
    while True:
        beat(sessions, "scheduler")
        tick(sessions, clock, settings)
        time.sleep(TICK_SECONDS)
