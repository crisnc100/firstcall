"""The worker, transcriber and scheduler processes, and the heartbeats their healthchecks
read."""

from __future__ import annotations

import logging
import socket
import sys
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

from firstcall.core.clock import Clock
from firstcall.db import Heartbeat
from firstcall.notes.reader import NoteReader, reread_notes
from firstcall.pipeline.jobs import TRANSCRIBE, claim, reap, run_one
from firstcall.pipeline.model_read import ModelReader
from firstcall.pipeline.reread import reread
from firstcall.settings import Settings
from firstcall.voice.job import expire_waiting, run_transcription

if TYPE_CHECKING:
    from firstcall.outbound.dispatch import Outbound
    from firstcall.voice.transcribe import Transcriber

log = logging.getLogger("firstcall.worker")
HEARTBEAT_SECONDS = 5.0
TICK_SECONDS = 30.0
HEALTHY_WITHIN_SECONDS = 60
TRANSCRIBER = "transcriber"


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
    and notes that wait for a model, if the model is available."""
    done = {"reaped": reap(sessions, clock, settings.max_attempts)}
    if reader is not None:
        done.update({f"reread_{k}": v for k, v in reread(sessions, reader).items()})
        notes = reread_notes(sessions, NoteReader(reader.gateway))
        done.update({f"reread_notes_{k}": v for k, v in notes.items()})
    return done


def alert_tick(sessions: sessionmaker[Any], clock: Clock, outbound: Outbound) -> dict[str, int]:
    """The scheduler's alert pass: fire due escalations, send what they queued, and send
    the email digests whose window has closed. A step that fails is logged and counted as
    zero, so the others still run and the scheduler keeps ticking."""
    from firstcall.board.escalate import escalate

    steps: dict[str, Callable[[], int]] = {
        "escalated": lambda: escalate(sessions, clock),
        "pushed": outbound.push_pending,
        "emailed": outbound.email_digests,
    }
    done: dict[str, int] = {}
    for name, step in steps.items():
        try:
            done[name] = step()
        except Exception:
            log.exception("alert step %s failed", name)
            done[name] = 0
    return done


def work(
    sessions: sessionmaker[Any],
    clock: Clock,
    settings: Settings,
    until_idle: bool = False,
    reader: ModelReader | None = None,
    outbound: Outbound | None = None,
) -> int:
    """Process jobs until stopped, or until the queue is empty when until_idle is set.
    Push goes out right after each job, since the alert-latency floor counts from intake;
    email digests are sent while the queue is empty. Returns how many jobs ran."""
    beat(sessions, "worker")
    print("firstcall worker ready", file=sys.stderr, flush=True)
    last_beat = time.monotonic()
    ran = 0

    def heartbeat() -> None:
        beat(sessions, "worker")

    while True:
        if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
            heartbeat()
            last_beat = time.monotonic()
        if run_one(
            sessions, clock, settings.job_lease_seconds, settings.max_attempts, reader, heartbeat
        ):
            ran += 1
            if outbound is not None:
                outbound.push_pending()
            continue
        if outbound is not None:
            outbound.push_pending()
            outbound.email_digests()
        if until_idle:
            return ran
        time.sleep(0.5)


def transcribe_work(
    sessions: sessionmaker[Any],
    clock: Clock,
    settings: Settings,
    transcriber: Transcriber,
    until_idle: bool = False,
) -> int:
    """The transcriber process: transcribe jobs only, one at a time, so the main worker's
    alerts never wait behind a memo. While a memo transcribes, the lease thread beats, so a
    long one never looks dead. Returns how many jobs ran."""
    beat(sessions, TRANSCRIBER)
    print("firstcall transcriber ready", file=sys.stderr, flush=True)
    last_beat = time.monotonic()
    ran = 0

    def heartbeat() -> None:
        beat(sessions, TRANSCRIBER)

    while True:
        if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
            heartbeat()
            last_beat = time.monotonic()
        lease, attempts = settings.job_lease_seconds, settings.max_attempts
        claimed = claim(sessions, clock, lease, attempts, kinds=(TRANSCRIBE,), skip=())
        if claimed is not None:
            run_transcription(sessions, clock, claimed, lease, attempts, transcriber, heartbeat)
            ran += 1
            continue
        if until_idle:
            return ran
        time.sleep(0.5)


def expire_tick(sessions: sessionmaker[Any], clock: Clock) -> dict[str, int]:
    """Memos waiting past their expiry fail and lose their audio. A failure is logged so the
    scheduler keeps ticking."""
    try:
        return {"expired_memos": expire_waiting(sessions, clock)}
    except Exception:
        log.exception("expiring waiting memos failed")
        return {"expired_memos": 0}


def followup_tick(sessions: sessionmaker[Any], clock: Clock) -> dict[str, int]:
    """The nightly follow-up job, for every team whose 05:00 has come and that has not run
    today. A failure is logged so the scheduler keeps ticking."""
    from firstcall.board.followups import run_nightly

    try:
        done = run_nightly(sessions, clock)
    except Exception:
        log.exception("the follow-up job failed")
        return {"followup_teams": 0}
    return {f"followup_{k}": v for k, v in done.items()}


def schedule(
    sessions: sessionmaker[Any],
    clock: Clock,
    settings: Settings,
    reader: ModelReader | None = None,
    outbound: Outbound | None = None,
) -> None:
    while True:
        beat(sessions, "scheduler")
        try:
            tick(sessions, clock, settings, reader)
        except Exception:
            # The reaper or a reread failed this pass; follow-ups and alerts still run, and
            # the next pass tries again.
            log.exception("the scheduler's tick failed")
        expire_tick(sessions, clock)
        followup_tick(sessions, clock)
        if outbound is not None:
            alert_tick(sessions, clock, outbound)
        time.sleep(TICK_SECONDS)


def model_reader(sessions: sessionmaker[Any], clock: Clock, settings: Settings) -> ModelReader:
    """The model read the settings ask for. Offline, switched off or without a key, every
    message takes the rules path and is marked for a reread."""
    from firstcall.model.gateway import Gateway
    from firstcall.pipeline.model_read import load_field_methods

    return ModelReader(Gateway.from_settings(settings, sessions, clock), load_field_methods())
