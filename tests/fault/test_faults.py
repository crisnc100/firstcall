"""Fault suite against compose: real worker processes, killed at a named point (F4, F5).

Each test gets its own database on the compose Postgres, so the stack's own worker never
sees these jobs. Containers reach that database by its compose host name.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import Job, Lead, LeadEvent, Message
from firstcall.pipeline.process import FAILPOINT_AFTER_LEAD_INSERT
from firstcall.seed import seed
from firstcall.settings import LOCAL_DATABASE_URL
from tests.conftest import FakeClock, upgrade
from tests.helpers import Pipeline, email

LEASE_SECONDS = 2


@pytest.fixture
def fault_db() -> Iterator[tuple[sessionmaker[Session], str]]:
    name = f"firstcall_fault_{uuid.uuid4().hex[:12]}"
    admin = create_engine(LOCAL_DATABASE_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    host_url = make_url(LOCAL_DATABASE_URL).set(database=name).render_as_string(hide_password=False)
    upgrade(host_url)
    engine = create_engine(host_url)
    container_url = (
        make_url(host_url).set(host="postgres", port=5432).render_as_string(hide_password=False)
    )
    yield sessionmaker(engine, expire_on_commit=False), container_url
    engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def compose_run(
    database_url: str, *command: str, failpoint: str = ""
) -> subprocess.CompletedProcess[str]:
    args = [
        "docker",
        "compose",
        "run",
        "--rm",
        "--no-deps",
        "-e",
        f"FIRSTCALL_DATABASE_URL={database_url}",
        "-e",
        f"FIRSTCALL_FAILPOINT={failpoint}",
        "-e",
        f"FIRSTCALL_JOB_LEASE_SECONDS={LEASE_SECONDS}",
        "worker",
        "firstcall",
        *command,
    ]
    return subprocess.run(args, capture_output=True, text=True, timeout=180)


def test_a_worker_killed_after_the_lead_insert_is_reaped_and_ends_with_one_lead(
    fault_db: tuple[sessionmaker[Session], str],
) -> None:
    sessions, container_url = fault_db
    pipe = Pipeline(sessions, FakeClock())
    message_id = pipe.store(
        "inbox", email("Ana Ruiz <ana@inbox.example>", "Juniper Mesa, please.\n\n(202) 555-0142")
    )

    killed = compose_run(
        container_url, "worker", "--until-idle", failpoint=FAILPOINT_AFTER_LEAD_INSERT
    )
    assert killed.returncode == 137, killed.stdout + killed.stderr
    assert f"FIRSTCALL_FAILPOINT fired {FAILPOINT_AFTER_LEAD_INSERT}" in killed.stderr
    with sessions() as session:
        job = session.scalars(select(Job)).one()
        assert (job.state, job.attempts) == ("running", 1)
        assert session.get_one(Message, message_id).status == "received"
        assert session.scalar(select(func.count()).select_from(Lead)) == 0

    time.sleep(LEASE_SECONDS + 1)
    reaped = compose_run(container_url, "tick")
    assert reaped.returncode == 0, reaped.stdout + reaped.stderr
    assert "'reaped': 1" in reaped.stdout

    finished = compose_run(container_url, "worker", "--until-idle")
    assert finished.returncode == 0, finished.stdout + finished.stderr
    assert "FIRSTCALL_FAILPOINT fired" not in finished.stderr
    with sessions() as session:
        job = session.scalars(select(Job)).one()
        assert (job.state, job.attempts) == ("done", 2)
        message = session.get_one(Message, message_id)
        assert (message.status, message.reason) == ("lead_created", "new lead")
        assert session.scalar(select(func.count()).select_from(Lead)) == 1
        created = session.scalar(
            select(func.count()).select_from(LeadEvent).where(LeadEvent.type == "lead.created")
        )
        assert created == 1


def test_a_failpoint_that_is_not_reached_changes_nothing(
    fault_db: tuple[sessionmaker[Session], str],
) -> None:
    sessions, container_url = fault_db
    seed(sessions)
    pipe = Pipeline(sessions, FakeClock())
    spam = pipe.store("inbox", email("Offers <win@prize-center.example>", "You won!"))
    run = compose_run(
        container_url, "worker", "--until-idle", failpoint=FAILPOINT_AFTER_LEAD_INSERT
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "FIRSTCALL_FAILPOINT fired" not in run.stderr
    with sessions() as session:
        message = session.get_one(Message, spam)
        assert (message.status, message.reason) == ("not_a_lead", "spam (spam_sender)")
