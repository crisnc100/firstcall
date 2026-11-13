"""Page details from the second browser QA run, in Chromium on compose.

- the top-bar unread count follows the list poll;
- after a rescue, focus is on the result, and the lead's history names the rescuer;
- the tray and the lead page show sources, categories and statuses in words, not slugs;
- Waiting longest counts teams, and names Unassigned apart from them;
- an opened digest shows its subject once, as the heading;
- on a phone the sign-in form starts 48 px from the top.
"""

from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.db import InboxEmail, Lead, Message, Team
from firstcall.seed import seed
from tests.conftest import FakeClock
from tests.fault.test_pages import PASSWORD, _free_port, _store, _wait_for
from tests.fault.test_pages import compose_db as compose_db  # a fixture, found by name
from tests.fault.test_pages_qa import ALL_INTERFACES, _sign_in, _start


def test_the_second_qa_round_in_a_browser(
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
            session, north.org_id, "mo@larkspur.example", "Mo Monitor", ["monitor"], PASSWORD
        )
        seller_id, org_id = seller.id, north.org_id

    pooled = _store(sessions, {"name": "Nell North", "listing": "Alder Ridge"})
    late = _store(sessions, {"name": "Una Late", "phone": "202-555-0171"})
    spam = [
        _store(sessions, {"name": name, "phone": phone, "website": "bot.example"})
        for name, phone in (("Sly Bot", "202-555-0172"), ("Spam Two", "202-555-0173"))
    ]

    run = uuid.uuid4().hex[:8]
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    names = [f"firstcall-details-worker-{run}", f"firstcall-details-api-{run}"]
    _start(names[0], container_url, "worker", "worker", FIRSTCALL_DIGEST_SECONDS="1")
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
                ids = [pooled, late, *spam]
                rows = session.scalars(select(Message.status).where(Message.id.in_(ids))).all()
                return len(rows) == 4 and all(s in ("lead_created", "not_a_lead") for s in rows)

        def api_up() -> bool:
            try:
                return httpx.get(f"{base}/healthz", timeout=2).status_code == 200
            except httpx.HTTPError:
                return False

        def mailed() -> bool:
            with sessions() as session:
                return (
                    session.scalars(
                        select(InboxEmail.id).where(InboxEmail.user_id == seller_id)
                    ).first()
                    is not None
                )

        _wait_for(processed, "the worker to process the messages")
        _wait_for(api_up, "the api container")
        _wait_for(mailed, "a digest in the seller's inbox")
        long_ago = datetime.now(UTC) - timedelta(minutes=40)
        with sessions.begin() as session:
            lead_id = session.get_one(Message, late).lead_id
            session.execute(
                update(Lead)
                .where(Lead.id == lead_id)
                .values(arrival_at=long_ago, pool_since=long_ago)
            )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()

            one = browser.new_context(base_url=base).new_page()
            _sign_in(one, "one@larkspur.example")
            badge = one.locator("a.inbox .count")
            expect(badge).to_have_text("1")
            with sessions.begin() as session:
                session.add(
                    InboxEmail(
                        org_id=org_id,
                        user_id=seller_id,
                        subject="A later alert",
                        text_body="A later alert\n",
                        html_body="<p>A later alert</p>",
                        created_at=datetime.now(UTC),
                    )
                )
            expect(badge).to_have_text("2", timeout=25_000)

            one.goto("/inbox")
            one.get_by_role("button", name="New lead in North").click()
            reader = one.locator("h2", has_text="New lead in North")
            expect(reader).to_be_visible()
            shown = one.locator(".msg").first.inner_text()
            assert not shown.startswith("New lead in North"), shown

            mo = browser.new_context(base_url=base).new_page()
            _sign_in(mo, "mo@larkspur.example")
            expect(mo.get_by_role("link", name="Una Late")).to_be_visible()
            mo.goto("/waiting")
            summary = mo.locator("p.num").first
            expect(summary).to_contain_text("in Unassigned.")
            assert "team" not in summary.inner_text(), summary.inner_text()

            mo.goto("/tray")
            expect(mo.get_by_role("link", name="Spam Two")).to_be_visible()
            expect(mo.get_by_text("site-form")).to_have_count(0)
            expect(mo.get_by_text("Web form").first).to_be_visible()
            mo.get_by_role("link", name="Sly Bot").click()
            mo.get_by_role("button", name="This is a lead: move it to the leads").click()
            expect(mo.locator("main").get_by_text("Moved to the leads.")).to_be_visible()
            focused = mo.evaluate("() => document.activeElement.textContent")
            assert focused.startswith("Moved to the leads."), focused

            mo.get_by_role("link", name="Open the lead").click()
            expect(mo.get_by_text("Rescued from Not-a-lead by Mo Monitor")).to_be_visible()
            expect(mo.get_by_text("lead created", exact=True)).to_have_count(0)
            expect(mo.get_by_text("lead", exact=True)).to_have_count(0)
            expect(mo.get_by_text("First message", exact=True)).to_be_visible()
            phone = browser.new_context(
                base_url=base, viewport={"width": 390, "height": 844}
            ).new_page()
            phone.goto("/signin")
            form = phone.locator(".signin").bounding_box()
            assert form is not None
            assert form["y"] <= 48, form

            browser.close()
    finally:
        for container in names:
            subprocess.run(["docker", "stop", container], capture_output=True, timeout=60)
