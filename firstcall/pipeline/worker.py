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
from firstcall.pipeline.model_read import ModelReader
from firstcall.pipeline.reread import reread
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


def tick(
    sessions: sessionmaker[Any],
    clock: Clock,
    settings: Settings,
    reader: ModelReader | None = None,
) -> dict[str, int]:
    """One scheduler pass: return jobs whose lease ran out, then reread rules-path messages
    if the model is available."""
    done = {"reaped": reap(sessions, clock, settings.max_attempts)}
    if reader is not None:
        done.update({f"reread_{k}": v for k, v in reread(sessions, reader).items()})
    return done


def work(
    sessions: sessionmaker[Any],
    clock: Clock,
    settings: Settings,
    until_idle: bool = False,
    reader: ModelReader | None = None,
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
        if run_one(sessions, clock, settings.job_lease_seconds, settings.max_attempts, reader):
            ran += 1
            continue
        if until_idle:
            return ran
        time.sleep(0.5)


def schedule(
    sessions: sessionmaker[Any],
    clock: Clock,
    settings: Settings,
    reader: ModelReader | None = None,
) -> None:
    while True:
        beat(sessions, "scheduler")
        tick(sessions, clock, settings, reader)
        time.sleep(TICK_SECONDS)


def model_reader(sessions: sessionmaker[Any], clock: Clock, settings: Settings) -> ModelReader:
    """The model read the settings ask for. Offline, switched off or without a key, every
    message takes the rules path and is marked for a reread."""
    from firstcall.model.gateway import Gateway

    return ModelReader(Gateway.from_settings(settings, sessions, clock))
