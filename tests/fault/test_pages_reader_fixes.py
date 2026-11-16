"""The page fixes from the phases 5-6 review, in a real browser on compose: editing one
profile field leaves the model's multi-line Notes alone, and a note's read shows up without
a reload."""

from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, datetime

import httpx
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.db import Lead, LeadProfile, Message, Note, Team
from firstcall.seed import seed
from tests.conftest import FakeClock
from tests.fault.test_pages import PASSWORD, _free_port, _store, _wait_for
from tests.fault.test_pages import compose_db as compose_db  # a fixture, found by name
from tests.fault.test_pages_suggestions import ALL_INTERFACES, MODEL, _run, _signed_in

MODEL_NOTES = "Likes the yard.\nWants a pool."
TIDIED = "Spoke with Dana about the yard."
# Lists and pending notes refresh every 15 seconds.
POLL_WAIT_MS = 25_000


def test_profile_notes_survive_an_edit_and_a_read_appears_without_a_reload(
    compose_db: tuple[sessionmaker[Session], str],
) -> None:
    sessions, container_url = compose_db
    seed(sessions)
    with sessions.begin() as session:
        north = session.scalars(select(Team).where(Team.slug == "north")).one()
        owner = accounts.create_user(
            session, north.org_id, "owner@larkspur.example", "Owner", [], PASSWORD
        )
        accounts.set_memberships(session, owner, {north.id: "salesperson"}, FakeClock())
        owner_id = owner.id
    stored = _store(sessions, {"name": "Dana Draft", "listing": "Alder Ridge"})

    run = uuid.uuid4().hex[:8]
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    no_key = {"FIRSTCALL_MODEL_MODE": "live", "FIRSTCALL_MODEL_NAME": MODEL}
    names = [f"firstcall-fixes-worker-{run}", f"firstcall-fixes-api-{run}"]
    _run(names[0], container_url, "worker", ["worker"], no_key)
    _run(names[1], container_url, "api", ["api", "--host", ALL_INTERFACES, "--port", "8000"],
         {}, port=port)  # fmt: skip
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
        now = datetime.now(UTC)
        with sessions.begin() as session:
            lead = session.get_one(Lead, session.get_one(Message, stored).lead_id)
            lead_id = lead.id
            session.execute(
                update(Lead)
                .where(Lead.id == lead_id)
                .values(state="owned", owner_id=owner_id, pool_since=None, claimed_at=now)
            )
            session.add(
                LeadProfile(
                    id=uuid.uuid4(),
                    org_id=lead.org_id,
                    lead_id=lead_id,
                    authority="unknown",
                    notes=MODEL_NOTES,
                    person_fields=[],
                    model_from={},
                    updated_at=now,
                )
            )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_context(base_url=base).new_page()
            _signed_in(page, "owner@larkspur.example")
            page.goto(f"/leads/{lead_id}")
            page.get_by_role("button", name="Edit profile").click()
            page.get_by_label("Needs", exact=True).fill("a garage")
            page.get_by_role("button", name="Save profile").click()
            expect(page.get_by_text("Profile saved.").first).to_be_visible()
            with sessions() as session:
                profile = session.scalars(select(LeadProfile)).one()
                assert profile.person_fields == ["need"]
                assert profile.notes == MODEL_NOTES

            page.get_by_label("Add a note").fill("Spoke with Dana about the yard.")
            page.get_by_role("button", name="Save note").click()
            expect(page.get_by_text("Note saved.").first).to_be_visible()

            def waiting() -> bool:
                with sessions() as session:
                    return session.scalars(select(Note.read_state)).one() == "needs_model"

            _wait_for(waiting, "the no-key worker to leave the note waiting")
            with sessions.begin() as session:
                session.execute(update(Note).values(read_state="read", clean_note=TIDIED))
            # No reload: the page notices the read on its own.
            expect(page.get_by_text(f"Tidied: {TIDIED}")).to_be_visible(timeout=POLL_WAIT_MS)
            expect(page.get_by_text("Needs a model key")).to_have_count(0)
            browser.close()
    finally:
        for name in names:
            subprocess.run(["docker", "stop", name], capture_output=True, timeout=60)
