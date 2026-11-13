"""Layout checks from browser QA, in Chromium on compose.

- at 320 px wide, every row's buttons stay inside the row and the page never scrolls sideways;
- a message with no text says so instead of drawing an empty block;
- in the users table, a user's name is set like the rest of the row, not like a header;
- the sign-in page shows the Firstcall wordmark on a phone.
"""

from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from playwright.sync_api import Page, ViewportSize, expect, sync_playwright
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.db import Lead, Message, Team
from firstcall.seed import seed
from tests.conftest import FakeClock
from tests.fault.test_pages import PASSWORD, _free_port, _store, _wait_for
from tests.fault.test_pages import compose_db as compose_db  # a fixture, found by name
from tests.fault.test_pages_qa import ALL_INTERFACES, _sign_in, _start

NARROW: ViewportSize = {"width": 320, "height": 700}


def _fits(page: Page) -> None:
    assert page.evaluate("() => document.documentElement.scrollWidth") <= NARROW["width"]
    overflow = page.evaluate(
        """() => [...document.querySelectorAll('li.row')].flatMap((row) => {
            const edge = row.getBoundingClientRect();
            return [...row.querySelectorAll('button, select')]
              .filter((el) => {
                const box = el.getBoundingClientRect();
                return box.right > edge.right + 0.5 || box.left < edge.left - 0.5;
              })
              .map((el) => el.textContent || el.getAttribute('aria-label'));
        })"""
    )
    assert overflow == []


def test_layout_holds_at_320_and_the_qa_details(
    compose_db: tuple[sessionmaker[Session], str],
) -> None:
    sessions, container_url = compose_db
    seed(sessions)
    with sessions.begin() as session:
        north = session.scalars(select(Team).where(Team.slug == "north")).one()
        seller = accounts.create_user(
            session, north.org_id, "one@larkspur.example", "One", [], PASSWORD
        )
        accounts.set_memberships(session, seller, {north.id: "salesperson"}, FakeClock())
        accounts.create_user(
            session, north.org_id, "admin@larkspur.example", "Ada", ["admin"], PASSWORD
        )

    pooled = _store(sessions, {"name": "Vera Verylate", "listing": "Alder Ridge"})
    unassigned = _store(sessions, {"name": "Omar Elsewhere", "phone": "202-555-0161"})

    run = uuid.uuid4().hex[:8]
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    names = [f"firstcall-layout-worker-{run}", f"firstcall-layout-api-{run}"]
    _start(names[0], container_url, "worker", "worker")
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
                ids = [pooled, unassigned]
                rows = session.scalars(select(Message.status).where(Message.id.in_(ids))).all()
                return len(rows) == 2 and all(s == "lead_created" for s in rows)

        def api_up() -> bool:
            try:
                return httpx.get(f"{base}/healthz", timeout=2).status_code == 200
            except httpx.HTTPError:
                return False

        _wait_for(processed, "the worker to process the messages")
        _wait_for(api_up, "the api container")
        # The widest timer text there is, so the narrow rows are tried at their fullest.
        long_ago = datetime.now(UTC) - timedelta(hours=23, minutes=59)
        with sessions.begin() as session:
            lead_ids = [session.get_one(Message, m).lead_id for m in (pooled, unassigned)]
            session.execute(
                update(Lead)
                .where(Lead.id.in_(lead_ids))
                .values(arrival_at=long_ago, pool_since=long_ago)
            )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()

            phone = browser.new_context(
                base_url=base, viewport={"width": 390, "height": 800}
            ).new_page()
            phone.goto("/signin")
            expect(phone.get_by_text("Firstcall", exact=True)).to_be_visible()

            narrow = browser.new_context(base_url=base, viewport=NARROW).new_page()
            _sign_in(narrow, "one@larkspur.example")
            expect(narrow.get_by_role("button", name="Claim Vera Verylate")).to_be_visible()
            _fits(narrow)

            admin = browser.new_context(base_url=base, viewport=NARROW).new_page()
            _sign_in(admin, "admin@larkspur.example")
            expect(admin.get_by_role("link", name="Omar Elsewhere")).to_be_visible()
            _fits(admin)

            admin.get_by_role("link", name="Omar Elsewhere").click()
            expect(admin.get_by_text("No message text.")).to_be_visible()
            empty = admin.evaluate(
                "() => [...document.querySelectorAll('.msg')]"
                ".filter((m) => !m.textContent.trim()).length"
            )
            assert empty == 0

            wide = browser.new_context(
                base_url=base, viewport={"width": 1280, "height": 800}
            ).new_page()
            _sign_in(wide, "admin@larkspur.example")
            expect(wide.get_by_role("link", name="Omar Elsewhere")).to_be_visible()
            wide.goto("/admin/users")
            name = wide.locator(".users-table tbody th").first
            email = wide.locator(".users-table tbody td").first
            expect(name).to_be_visible()
            style = "(el) => [getComputedStyle(el).fontSize, getComputedStyle(el).backgroundColor]"
            name_size, name_bg = name.evaluate(style)
            email_size, email_bg = email.evaluate(style)
            assert name_size == email_size
            assert name_bg == email_bg
            browser.close()
    finally:
        for container in names:
            subprocess.run(["docker", "stop", container], capture_output=True, timeout=60)
