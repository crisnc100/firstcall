"""Lead list fixes from review slice a69983, in Chromium on compose.

- a lead with no name still links to its page, labelled by its phone;
- a lost claim whose lead comes back to the pool gets its Claim button back.
"""

from __future__ import annotations

import re
import subprocess
import uuid
from datetime import UTC, datetime

import httpx
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.db import Lead, Message, Team
from firstcall.seed import seed
from tests.conftest import FakeClock
from tests.fault.test_pages import PASSWORD, _free_port, _store, _wait_for
from tests.fault.test_pages import compose_db as compose_db  # a fixture, found by name
from tests.fault.test_pages_qa import ALL_INTERFACES, _sign_in, _start


def test_nameless_links_and_a_lost_claim_recovers(
    compose_db: tuple[sessionmaker[Session], str],
) -> None:
    sessions, container_url = compose_db
    seed(sessions)
    with sessions.begin() as session:
        north = session.scalars(select(Team).where(Team.slug == "north")).one()
        ids = {}
        for email in ("one@larkspur.example", "two@larkspur.example"):
            user = accounts.create_user(
                session, north.org_id, email, email.split("@")[0].title(), [], PASSWORD
            )
            accounts.set_memberships(session, user, {north.id: "salesperson"}, FakeClock())
            ids[email] = user.id

    nameless = _store(sessions, {"phone": "202-555-0191", "listing": "Alder Ridge"})
    contested = _store(sessions, {"name": "Rita Returns", "listing": "Foxglove Row"})

    run = uuid.uuid4().hex[:8]
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    names = [f"firstcall-leadlist-worker-{run}", f"firstcall-leadlist-api-{run}"]
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
                rows = session.scalars(
                    select(Message.status).where(Message.id.in_([nameless, contested]))
                ).all()
                return len(rows) == 2 and all(s == "lead_created" for s in rows)

        def api_up() -> bool:
            try:
                return httpx.get(f"{base}/healthz", timeout=2).status_code == 200
            except httpx.HTTPError:
                return False

        _wait_for(processed, "the worker to process the messages")
        _wait_for(api_up, "the api container")
        with sessions.begin() as session:
            nameless_lead = session.get_one(Message, nameless).lead_id
            session.execute(
                update(Lead)
                .where(Lead.id == nameless_lead)
                .values(state="owned", owner_id=ids["one@larkspur.example"], pool_since=None)
            )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            one = browser.new_context(base_url=base).new_page()
            two = browser.new_context(base_url=base).new_page()
            _sign_in(one, "one@larkspur.example")
            _sign_in(two, "two@larkspur.example")
            for page in (one, two):
                expect(page).not_to_have_url(re.compile(r"/signin$"))

            one.goto("/mine")
            nameless_link = one.locator("li.row a.name")
            expect(nameless_link).to_have_count(1)
            expect(nameless_link).to_contain_text("202")
            expect(nameless_link).to_have_attribute("href", f"/leads/{nameless_lead}?from=mine")

            one.goto("/pool")
            for page in (one, two):
                expect(page.get_by_role("link", name="Rita Returns")).to_be_visible()
            one.get_by_role("button", name="Claim Rita Returns").click()
            expect(one.get_by_text("You own this lead now.").first).to_be_visible()
            two.get_by_role("button", name="Claim Rita Returns").click()
            row = two.locator("li.row", has=two.get_by_role("link", name="Rita Returns"))
            expect(row.get_by_text("Already claimed by a teammate")).to_be_visible()

            # Back in the pool while the lost row is still on screen.
            with sessions.begin() as session:
                rita = session.get_one(Message, contested).lead_id
                session.execute(
                    update(Lead)
                    .where(Lead.id == rita)
                    .values(state="pool", owner_id=None, pool_since=datetime.now(UTC))
                )
            expect(row.get_by_role("button", name="Claim Rita Returns")).to_be_visible(
                timeout=30_000
            )
            expect(row.get_by_text("Already claimed by a teammate")).to_have_count(0)
            browser.close()
    finally:
        for container in names:
            subprocess.run(["docker", "stop", container], capture_output=True, timeout=60)
