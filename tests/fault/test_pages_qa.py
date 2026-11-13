"""The page fixes from browser QA, in a real browser on compose.

- the sign-in password field is wide enough to type in, with Show inside it;
- after a lost claim, focus is on the row's message;
- reading a digest updates the top-bar unread count, and its links use the base URL;
- a lead placed from Unassigned starts a new stay: it reads New and is not on Waiting
  longest, while a lead that reads Late is;
- with no push keys, the alerts page says alerts aren't set up.
"""

from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.db import InboxEmail, Lead, Message, Team
from firstcall.seed import seed
from tests.conftest import FakeClock
from tests.fault.test_pages import PASSWORD, _free_port, _store, _wait_for
from tests.fault.test_pages import compose_db as compose_db  # a fixture, found by name

# The api listens on every interface inside its container; the host maps only 127.0.0.1.
ALL_INTERFACES = "0.0.0.0"  # noqa: S104


def _start(name: str, database_url: str, service: str, *command: str, **env: str) -> None:
    args = ["docker", "compose", "run", "-d", "--rm", "--no-deps", "--name", name]
    if "port" in env:
        args += ["-p", f"127.0.0.1:{env.pop('port')}:8000"]
    for key, value in {"FIRSTCALL_DATABASE_URL": database_url, **env}.items():
        args += ["-e", f"{key}={value}"]
    started = subprocess.run(
        [*args, service, "firstcall", *command], capture_output=True, text=True, timeout=120
    )
    assert started.returncode == 0, started.stderr


def _sign_in(page: Page, email: str) -> None:
    page.goto("/signin")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()


def test_the_qa_fixes_in_a_browser(compose_db: tuple[sessionmaker[Session], str]) -> None:
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

    contested = _store(sessions, {"name": "Cora Contested", "listing": "Alder Ridge"})
    placed = _store(sessions, {"name": "Pia Placed", "phone": "202-555-0151"})
    stale = _store(sessions, {"name": "Otto Overdue", "phone": "202-555-0152"})

    run = uuid.uuid4().hex[:8]
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    names = [f"firstcall-qa-worker-{run}", f"firstcall-qa-api-{run}"]
    _start(
        names[0],
        container_url,
        "worker",
        "worker",
        FIRSTCALL_DIGEST_SECONDS="1",
        FIRSTCALL_BASE_URL=base,
    )
    _start(
        names[1],
        container_url,
        "api",
        "api",
        "--host",
        ALL_INTERFACES,
        "--port",
        "8000",
        port=str(port),
    )
    try:

        def processed() -> bool:
            with sessions() as session:
                ids = [contested, placed, stale]
                rows = session.scalars(select(Message.status).where(Message.id.in_(ids))).all()
                return len(rows) == 3 and all(s == "lead_created" for s in rows)

        def api_up() -> bool:
            try:
                return httpx.get(f"{base}/healthz", timeout=2).status_code == 200
            except httpx.HTTPError:
                return False

        def mailed() -> bool:
            with sessions() as session:
                return session.scalars(select(InboxEmail.id)).first() is not None

        _wait_for(processed, "the worker to process the messages")
        _wait_for(api_up, "the api container")
        _wait_for(mailed, "a digest in the inbox")
        long_ago = datetime.now(UTC) - timedelta(minutes=40)
        with sessions.begin() as session:
            for message_id in (placed, stale):
                lead_id = session.get_one(Message, message_id).lead_id
                session.execute(
                    update(Lead)
                    .where(Lead.id == lead_id)
                    .values(arrival_at=long_ago, pool_since=long_ago)
                )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for width in (1280, 390):
                page = browser.new_context(
                    base_url=base, viewport={"width": width, "height": 800}
                ).new_page()
                page.goto("/signin")
                field = page.get_by_label("Password", exact=True).bounding_box()
                show = page.get_by_role("button", name="Show").bounding_box()
                assert field is not None
                assert show is not None
                assert field["width"] >= 250, (width, field)
                assert show["width"] < 100, (width, show)
                inside = (
                    field["x"] <= show["x"]
                    and show["x"] + show["width"] <= field["x"] + field["width"]
                )
                assert inside, (width, field, show)

            one = browser.new_context(base_url=base).new_page()
            two = browser.new_context(base_url=base).new_page()
            _sign_in(one, "one@larkspur.example")
            _sign_in(two, "two@larkspur.example")
            for page in (one, two):
                expect(page.get_by_role("link", name="Cora Contested")).to_be_visible()
            one.get_by_role("button", name="Claim Cora Contested").click()
            expect(one.get_by_text("You own this lead now.").first).to_be_visible()
            two.get_by_role("button", name="Claim Cora Contested").click()
            expect(two.get_by_text("Already claimed by a teammate").first).to_be_visible()
            focused = two.evaluate("() => document.activeElement.textContent")
            assert focused == "Already claimed by a teammate"

            one.goto("/inbox")
            badge = one.locator("a.inbox .count")
            expect(badge).to_be_visible()
            before = int(badge.inner_text())
            one.get_by_role("button", name="Unread,").first.click()
            expect(one.get_by_text(f"{base}/leads/").first).to_be_visible()
            if before == 1:
                expect(badge).to_be_hidden()
            else:
                expect(badge).to_have_text(str(before - 1))

            monitor = browser.new_context(base_url=base).new_page()
            _sign_in(monitor, "monitor@larkspur.example")
            row = monitor.locator("li.row", has=monitor.get_by_role("link", name="Pia Placed"))
            row.get_by_label("Team for Pia Placed").select_option(label="North")
            row.get_by_role("button", name="Place").click()
            expect(monitor.get_by_text("Placed in North.").first).to_be_visible()
            monitor.goto("/waiting")
            expect(monitor.get_by_role("link", name="Otto Overdue").first).to_be_visible()
            expect(monitor.get_by_role("link", name="Pia Placed")).to_have_count(0)
            monitor.get_by_label("Show all unclaimed").check()
            placed_row = monitor.locator(
                "li.row", has=monitor.get_by_role("link", name="Pia Placed")
            )
            expect(placed_row.locator(".timer .badge")).to_have_text("New")

            monitor.goto("/alerts")
            expect(monitor.get_by_text("Alerts aren't set up on this server.")).to_be_visible()
            browser.close()
    finally:
        for name in names:
            subprocess.run(["docker", "stop", name], capture_output=True, timeout=60)
