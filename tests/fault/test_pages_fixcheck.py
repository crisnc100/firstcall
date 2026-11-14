"""The page fixes from the phases 5-6 fix check, in a real browser on compose: a refresh
never wipes a picked "Called", and a poll answer that arrives after a newer save is dropped
without stopping the polling."""

from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from playwright.sync_api import APIResponse, Route, expect, sync_playwright
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.db import Lead, Message, Note, Team
from firstcall.seed import seed
from tests.conftest import FakeClock
from tests.fault.test_pages import PASSWORD, _free_port, _store, _wait_for
from tests.fault.test_pages import compose_db as compose_db  # a fixture, found by name
from tests.fault.test_pages_reader_fixes import POLL_WAIT_MS
from tests.fault.test_pages_suggestions import ALL_INTERFACES, MODEL, _run, _signed_in

FIRST, SECOND, THIRD = "Left her a voicemail.", "Asked about the yard.", "Sent the floor plans."


def _set_read(sessions: sessionmaker[Session], text: str, clean: str) -> None:
    with sessions.begin() as session:
        session.execute(
            update(Note).where(Note.text == text).values(read_state="read", clean_note=clean)
        )


def test_a_refresh_keeps_a_picked_action_and_drops_a_stale_poll(
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
    names = [f"firstcall-fixcheck-worker-{run}", f"firstcall-fixcheck-api-{run}"]
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

        def waiting(text: str) -> bool:
            with sessions() as session:
                state = session.scalar(select(Note.read_state).where(Note.text == text))
                return state == "needs_model"

        _wait_for(processed, "the worker to process the message")
        _wait_for(api_up, "the api container")
        with sessions.begin() as session:
            lead_id = session.get_one(Message, stored).lead_id
            session.execute(
                update(Lead)
                .where(Lead.id == lead_id)
                .values(state="owned", owner_id=owner_id, pool_since=None,
                        claimed_at=datetime.now(UTC))
            )  # fmt: skip
        lead_path = f"/api/leads/{lead_id}"

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_context(base_url=base).new_page()
            _signed_in(page, "owner@larkspur.example")
            page.goto(f"/leads/{lead_id}")

            def save_note(text: str) -> None:
                page.get_by_label("Add a note").fill(text)
                page.get_by_role("button", name="Save note").click()
                expect(page.get_by_text("Note saved.").first).to_be_visible()

            def a_poll() -> Any:
                return page.expect_response(
                    lambda r: r.url.endswith(lead_path) and r.request.method == "GET",
                    timeout=POLL_WAIT_MS,
                )

            # A picked "Called" with no text yet, and nothing focused: a poll that finds a
            # changed read leaves the form alone.
            save_note(FIRST)
            _wait_for(lambda: waiting(FIRST), "the no-key worker to leave the note waiting")
            page.get_by_label("This note records").select_option("call")
            page.locator("h1").click()
            _set_read(sessions, FIRST, "Left a voicemail.")
            with a_poll():
                pass
            page.wait_for_timeout(1000)
            expect(page.get_by_label("This note records")).to_have_value("call")
            expect(page.get_by_text("Tidied: Left a voicemail.")).to_have_count(0)
            # Set back to no contact, the next poll redraws.
            page.get_by_label("This note records").select_option("")
            page.locator("h1").click()
            expect(page.get_by_text("Tidied: Left a voicemail.")).to_be_visible(
                timeout=POLL_WAIT_MS
            )

            # A poll answer held until after a newer save must not replace the newer page.
            save_note(SECOND)
            _wait_for(lambda: waiting(SECOND), "the second note to wait")
            held: list[tuple[Route, APIResponse]] = []
            armed = [True]

            def hold(route: Route) -> None:
                if armed[0] and route.request.method == "GET":
                    armed[0] = False
                    held.append((route, route.fetch()))
                    return
                route.continue_()

            page.route(f"**{lead_path}", hold)
            _set_read(sessions, SECOND, "Asked about the yard and garden.")
            _wait_for(lambda: page.wait_for_timeout(250) or bool(held), "a poll to be held")
            save_note(THIRD)
            expect(page.get_by_text(THIRD)).to_be_visible()
            route, stale = held[0]
            route.fulfill(response=stale)
            page.wait_for_timeout(1500)
            expect(page.get_by_text(THIRD)).to_be_visible()
            # Polling carries on: the third note's read still shows up.
            _wait_for(lambda: waiting(THIRD), "the third note to wait")
            _set_read(sessions, THIRD, "Sent the plans.")
            expect(page.get_by_text("Tidied: Sent the plans.")).to_be_visible(timeout=POLL_WAIT_MS)
            browser.close()
    finally:
        for name in names:
            subprocess.run(["docker", "stop", name], capture_output=True, timeout=60)
