"""Working a lead in a real browser on compose: the owner adds a note that logs a call and
sees the contact and the time to first contact, marks the lead lost with a reason, a team
lead reopens it, and an admin deletes it. A note holding a script tag shows as text."""

from __future__ import annotations

import re
import subprocess
import uuid

import httpx
from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.db import Lead, Message, Team
from firstcall.seed import seed
from tests.conftest import FakeClock
from tests.fault.test_pages import PASSWORD, _free_port, _store, _wait_for
from tests.fault.test_pages import compose_db as compose_db  # a fixture, found by name
from tests.fault.test_pages_qa import _sign_in, _start

ALL_INTERFACES = "0.0.0.0"  # noqa: S104
SCRIPT_NOTE = "<script>window.__pwned = 1</script>Left a voicemail."


def _signed_in(page: Page, email: str) -> None:
    _sign_in(page, email)
    expect(page).not_to_have_url(re.compile(r"/signin"))


def test_notes_outcome_reopen_and_delete_in_a_browser(
    compose_db: tuple[sessionmaker[Session], str],
) -> None:
    sessions, container_url = compose_db
    seed(sessions)
    with sessions.begin() as session:
        north = session.scalars(select(Team).where(Team.slug == "north")).one()
        for email, roles, team_role in (
            ("owner@larkspur.example", [], "salesperson"),
            ("teamlead@larkspur.example", [], "team_lead"),
            ("admin@larkspur.example", ["admin"], None),
        ):
            user = accounts.create_user(
                session, north.org_id, email, email.split("@")[0].title(), roles, PASSWORD
            )
            if team_role:
                accounts.set_memberships(session, user, {north.id: team_role}, FakeClock())
    stored = _store(sessions, {"name": "Nora Notes", "listing": "Alder Ridge"})

    run = uuid.uuid4().hex[:8]
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    names = [f"firstcall-notes-worker-{run}", f"firstcall-notes-api-{run}"]
    _start(names[0], container_url, "worker", "worker", FIRSTCALL_BASE_URL=base)
    _start(
        names[1], container_url, "api", "api", "--host", ALL_INTERFACES, "--port", "8000",
        port=str(port),
    )  # fmt: skip
    try:

        def processed() -> bool:
            with sessions() as session:
                return session.get_one(Message, stored).status == "lead_created"

        def api_up() -> bool:
            try:
                return httpx.get(f"{base}/healthz", timeout=2).status_code == 200
            except httpx.HTTPError:
                return False

        _wait_for(processed, "the worker to process the message")
        _wait_for(api_up, "the api container")
        with sessions() as session:
            lead_id = session.get_one(Message, stored).lead_id
        assert lead_id is not None
        path = f"/leads/{lead_id}"

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            owner = browser.new_context(base_url=base).new_page()
            _sign_in(owner, "owner@larkspur.example")
            owner.get_by_role("button", name="Claim Nora Notes").click()
            expect(owner.get_by_text("You own this lead now.").first).to_be_visible()

            owner.goto(path)
            owner.get_by_label("Add a note").fill("Called Nora about the loft.")
            owner.get_by_label("This note records").select_option(label="Called")
            owner.get_by_role("button", name="Save note").click()
            expect(owner.get_by_text("Note saved and contact logged.").first).to_be_visible()
            expect(owner.get_by_text("Called · contact logged")).to_be_visible()
            expect(owner.locator(".first-contact")).to_contain_text("First contact after")
            expect(owner.get_by_text("Called: logged by Owner")).to_be_visible()

            owner.get_by_label("Add a note").fill(SCRIPT_NOTE)
            owner.get_by_role("button", name="Save note").click()
            expect(owner.get_by_text(SCRIPT_NOTE)).to_be_visible()
            assert owner.evaluate("() => window.__pwned") is None
            expect(owner.get_by_role("heading", name="Notes (2)")).to_be_visible()

            owner.get_by_role("button", name="Mark lost…").click()
            owner.get_by_label("Why it was lost").select_option(label="Timing")
            owner.get_by_role("button", name="Mark lost", exact=True).click()
            expect(owner.get_by_text("Marked lost.").first).to_be_visible()
            expect(owner.get_by_text("Marked lost (timing) by Owner")).to_be_visible()

            team_lead = browser.new_context(base_url=base).new_page()
            _signed_in(team_lead, "teamlead@larkspur.example")
            team_lead.goto(path)
            expect(team_lead.get_by_text("Called Nora about the loft.")).to_be_visible()
            team_lead.get_by_role("button", name="Reopen").click()
            expect(team_lead.get_by_text("Reopened.").first).to_be_visible()
            expect(team_lead.get_by_text("Reopened to its owner by Teamlead")).to_be_visible()
            with sessions() as session:
                assert session.get_one(Lead, lead_id).state == "owned"

            admin = browser.new_context(base_url=base).new_page()
            _signed_in(admin, "admin@larkspur.example")
            admin.goto(path)
            admin.get_by_role("button", name="Delete lead…").click()
            admin.get_by_role("button", name="Delete this lead for good").click()
            expect(admin.get_by_text("Lead deleted.").first).to_be_visible()
            expect(admin).not_to_have_url(f"{base}{path}")
            with sessions() as session:
                row = session.get_one(Lead, lead_id)
                assert row.deleted_at is not None
                assert row.name is None
            browser.close()
    finally:
        for name in names:
            subprocess.run(["docker", "stop", name], capture_output=True, timeout=60)
