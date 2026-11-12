"""The pages in a real browser, against an api and a worker container on compose (F29).

- a salesperson signs in, sees the pool and claims;
- a second browser, with the same pool open, sees "already claimed";
- the monitor places a lead from Unassigned and rescues one from the Not-a-lead tray;
- a buyer name holding a <script> tag renders as text and runs nothing.

The containers get their own database on the compose Postgres, so the stack's own data
never shows up here and nothing here is left behind.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator

import httpx
import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.db import Lead, Message, Source, Team
from firstcall.intake.store import accept
from firstcall.seed import seed
from firstcall.settings import LOCAL_DATABASE_URL
from tests.conftest import FakeClock, upgrade

PASSWORD = "correct horse battery"  # pragma: allowlist secret
SCRIPT_NAME = "<script>window.__pwned = 1</script>"


@pytest.fixture
def compose_db() -> Iterator[tuple[sessionmaker[Session], str]]:
    name = f"firstcall_pages_{uuid.uuid4().hex[:12]}"
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


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _container(name: str, database_url: str, *command: str, port: int | None = None) -> None:
    args = ["docker", "compose", "run", "-d", "--rm", "--no-deps", "--name", name]
    if port is not None:
        args += ["-p", f"127.0.0.1:{port}:8000"]
    args += ["-e", f"FIRSTCALL_DATABASE_URL={database_url}"]
    service = "api" if port is not None else "worker"
    started = subprocess.run(
        [*args, service, "firstcall", *command], capture_output=True, text=True, timeout=120
    )
    assert started.returncode == 0, started.stderr


def _store(sessions: sessionmaker[Session], payload: dict[str, str]) -> uuid.UUID:
    with sessions() as session:
        source = session.scalars(select(Source).where(Source.slug == "site-form")).one()
    body = json.dumps(payload).encode()
    return accept(sessions, source, body, "application/json", {}).message_id


def _wait_for(check: object, what: str, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while not check():  # type: ignore[operator]
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.3)


def _sign_in(browser: Browser, base: str, email: str) -> Page:
    page = browser.new_context(base_url=base).new_page()
    page.goto("/signin")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    return page


def test_claim_already_claimed_place_rescue_and_escaped_names(
    compose_db: tuple[sessionmaker[Session], str],
) -> None:
    sessions, container_url = compose_db
    seed(sessions)
    with sessions.begin() as session:
        north = session.scalars(select(Team).where(Team.slug == "north")).one()
        for email, roles, team_role in (
            ("one@larkspur.example", [], "salesperson"),
            ("two@larkspur.example", [], "salesperson"),
            ("monitor@larkspur.example", ["monitor"], None),
        ):
            user = accounts.create_user(
                session, north.org_id, email, email.split("@")[0].title(), roles, PASSWORD
            )
            if team_role:
                accounts.set_memberships(session, user, {north.id: team_role}, FakeClock())

    claim_me = _store(
        sessions, {"name": "Ana Claimtest", "listing": "Cedar Point Lofts", "phone": "202-555-0111"}
    )
    hostile = _store(
        sessions, {"name": SCRIPT_NAME, "listing": "Alder Ridge", "phone": "202-555-0112"}
    )
    place_me = _store(
        sessions, {"name": "Una Placeme", "phone": "202-555-0113", "message": "Hello"}
    )
    rescue_me = _store(
        sessions,
        {"name": "Sam Rescueme", "phone": "202-555-0114", "website": "bot-filled.example"},
    )

    run = uuid.uuid4().hex[:8]
    worker, api = f"firstcall-pages-worker-{run}", f"firstcall-pages-api-{run}"
    port = _free_port()
    _container(worker, container_url, "worker")
    _container(api, container_url, "api", "--host", "0.0.0.0", "--port", "8000", port=port)  # noqa: S104
    base = f"http://127.0.0.1:{port}"
    try:

        def processed() -> bool:
            with sessions() as session:
                ids = [claim_me, hostile, place_me, rescue_me]
                statuses = session.scalars(select(Message.status).where(Message.id.in_(ids)))
                return all(s in ("lead_created", "not_a_lead") for s in statuses)

        def api_up() -> bool:
            try:
                return httpx.get(f"{base}/healthz", timeout=2).status_code == 200
            except httpx.HTTPError:
                return False

        _wait_for(processed, "the worker to process the messages")
        _wait_for(api_up, "the api container")
        with sessions() as session:
            claim_lead = session.get_one(Message, claim_me).lead_id
            placed_lead = session.get_one(Message, place_me).lead_id
            assert session.get_one(Message, rescue_me).status == "not_a_lead"

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            one = _sign_in(browser, base, "one@larkspur.example")
            two = _sign_in(browser, base, "two@larkspur.example")
            for page in (one, two):
                expect(page.get_by_role("heading", name="North pool")).to_be_visible()
                expect(page.get_by_role("link", name="Ana Claimtest")).to_be_visible()

            # A buyer name with a script tag is shown as text and never runs.
            expect(one.get_by_role("link", name=SCRIPT_NAME)).to_be_visible()
            assert one.evaluate("() => window.__pwned") is None

            one.get_by_role("button", name="Claim Ana Claimtest").click()
            expect(one.get_by_text("You own this lead now.").first).to_be_visible()
            two.get_by_role("button", name="Claim Ana Claimtest").click()
            expect(two.get_by_text("Already claimed by a teammate").first).to_be_visible()

            monitor = _sign_in(browser, base, "monitor@larkspur.example")
            expect(monitor.get_by_role("heading", name="Unassigned")).to_be_visible()
            row = monitor.locator("li.row", has=monitor.get_by_role("link", name="Una Placeme"))
            row.get_by_label("Team for Una Placeme").select_option(label="North")
            row.get_by_role("button", name="Place").click()
            expect(monitor.get_by_text("Placed in North.").first).to_be_visible()

            monitor.goto("/tray")
            monitor.get_by_role("link", name="Sam Rescueme").click()
            monitor.get_by_role("button", name="This is a lead: move it to the leads").click()
            expect(monitor.get_by_text("Moved to the leads.")).to_be_visible()
            browser.close()

        with sessions() as session:
            claimed = session.get_one(Lead, claim_lead)
            assert claimed.state == "owned"
            owner = session.scalars(select(Lead.owner_id).where(Lead.id == claim_lead)).one()
            assert owner is not None
            assert session.get_one(Lead, placed_lead).team_id == north.id
            assert session.get_one(Message, rescue_me).status == "lead_created"
    finally:
        for name in (worker, api):
            subprocess.run(["docker", "stop", name], capture_output=True, timeout=60)
