"""On compose, 50 rules-path leads reach the push request with p95 at most 2 seconds (F28).

The clock starts when intake's commit returns on the host and stops when the fake push
service, also on the host, receives the request, so both ends read the same clock. A real
worker container does the work, with its own database on the compose Postgres.
"""

from __future__ import annotations

import json
import math
import subprocess
import time
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.db import PushSubscription, Team
from firstcall.settings import LOCAL_DATABASE_URL
from tests.board_helpers import Device, FakePushService, b64, vapid_keys
from tests.conftest import FakeClock, upgrade
from tests.helpers import Pipeline

LEADS = 50
GAP_SECONDS = 0.1
P95_LIMIT = 2.0


@pytest.fixture
def compose_db() -> Iterator[tuple[sessionmaker[Session], str]]:
    name = f"firstcall_latency_{uuid.uuid4().hex[:12]}"
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


@pytest.fixture
def push() -> Iterator[FakePushService]:
    # Bound on every interface so the worker container can reach it through the host gateway.
    service = FakePushService(host="0.0.0.0")  # noqa: S104
    yield service
    service.close()


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def test_fifty_rules_path_leads_reach_the_push_request_within_two_seconds(
    compose_db: tuple[sessionmaker[Session], str], push: FakePushService
) -> None:
    sessions, container_url = compose_db
    pipe = Pipeline(sessions, FakeClock())
    with sessions.begin() as session:
        north = session.scalars(select(Team).where(Team.slug == "north")).one()
        user = accounts.create_user(session, north.org_id, "seller@larkspur.example", "Seller")
        accounts.set_memberships(session, user, {north.id: "salesperson"}, FakeClock())
    device = Device(f"http://host.docker.internal:{push.port}/push/{uuid.uuid4().hex}")
    with sessions.begin() as session:
        session.add(
            PushSubscription(
                org_id=user.org_id,
                user_id=user.id,
                endpoint=device.endpoint,
                p256dh=device.p256dh,
                auth=b64(device.auth),
                created_at=FakeClock().now(),
            )
        )
    private, _ = vapid_keys()
    name = f"firstcall-latency-{uuid.uuid4().hex[:8]}"
    started = subprocess.run(
        [
            "docker", "compose", "run", "-d", "--rm", "--no-deps", "--name", name,
            "-e", f"FIRSTCALL_DATABASE_URL={container_url}",
            "-e", f"FIRSTCALL_VAPID_PRIVATE_KEY={private}",
            "-e", "FIRSTCALL_PUSH_ALLOW_HTTP=1",
            "worker", "firstcall", "worker",
        ],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert started.returncode == 0, started.stderr
    try:
        deadline = time.monotonic() + 60
        while "firstcall worker ready" not in _logs(name):
            assert time.monotonic() < deadline, _logs(name)
            time.sleep(0.2)

        committed: dict[str, float] = {}
        for n in range(LEADS):
            buyer = f"Buyer{n:02d} Latency"
            payload = {"name": buyer, "email": f"b{n}@buyers.example", "listing": "Alder Ridge"}
            pipe.form("site-form", payload)
            committed[buyer] = time.time()
            time.sleep(GAP_SECONDS)

        deadline = time.monotonic() + 60
        while len(push.to(device)) < LEADS:
            assert time.monotonic() < deadline, f"{len(push.to(device))} pushes; {_logs(name)}"
            time.sleep(0.1)
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True, timeout=60)

    latencies = {}
    for received in push.to(device):
        body = device.read(received.body)["body"]
        buyer = body.removesuffix(" Alder Ridge")
        latencies[buyer] = received.at - committed[buyer]
    assert set(latencies) == set(committed)
    figure = {
        "leads": LEADS,
        "p50_seconds": round(sorted(latencies.values())[LEADS // 2], 3),
        "p95_seconds": round(p95(list(latencies.values())), 3),
        "max_seconds": round(max(latencies.values()), 3),
    }
    print(f"alert latency {json.dumps(figure)}")
    assert figure["p95_seconds"] <= P95_LIMIT, figure


def _logs(name: str) -> str:
    result = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=30)
    return result.stdout + result.stderr
