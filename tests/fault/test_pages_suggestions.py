"""Suggestions in a real browser on compose. A worker with no model key leaves the note
saying it needs one; a scheduler pass in replay mode then reads it from the model's recorded
answer, and the owner confirms the suggested call.

The note is `unit-page` in evals/gold/unit-notes.jsonl, and its answer was recorded once at
that file's fixed date. The test dates the note to that day so the reader sends the same
request, and replays the committed recordings with no model call.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.db import Contact, Message, Note, NoteSuggestion, Team
from firstcall.evaluation.note_eval import RECORDINGS, UNIT_DATE
from firstcall.seed import seed
from tests.conftest import FakeClock
from tests.fault.test_pages import PASSWORD, _free_port, _store, _wait_for
from tests.fault.test_pages import compose_db as compose_db  # a fixture, found by name
from tests.fault.test_pages_qa import _sign_in

ALL_INTERFACES = "0.0.0.0"  # noqa: S104
MODEL = "claude-haiku-5-5"
UNIT_NOTES = Path(__file__).resolve().parents[2] / "evals" / "gold" / "unit-notes.jsonl"
# The recorded answer's tidied note and its suggested call.
CLEAN = (
    "Called Dana this morning. Budget is around $450k. Wants three bedrooms and a garage. "
    "Hoping to move by June."
)
SUGGESTED = "Suggested: Called — “Called Dana this morning”"


def _page_note() -> str:
    rows = [json.loads(line) for line in UNIT_NOTES.read_text().splitlines()]
    return next(row["text"] for row in rows if row["id"] == "unit-page")


def _run(
    name: str,
    url: str,
    service: str,
    args: list[str],
    env: dict[str, str],
    volume: str | None = None,
    port: int | None = None,
    detach: bool = True,
) -> None:
    command = ["docker", "compose", "run", "--rm", "--no-deps", "--name", name]
    if detach:
        command.append("-d")
    if port is not None:
        command += ["-p", f"127.0.0.1:{port}:8000"]
    if volume is not None:
        command += ["-v", volume]
    for key, value in {"FIRSTCALL_DATABASE_URL": url, **env}.items():
        command += ["-e", f"{key}={value}"]
    done = subprocess.run(
        [*command, service, "firstcall", *args], capture_output=True, text=True, timeout=180
    )
    assert done.returncode == 0, done.stderr


def _signed_in(page: Page, email: str) -> None:
    _sign_in(page, email)
    expect(page).not_to_have_url(re.compile(r"/signin"))


def test_a_suggestion_is_confirmed_in_a_browser(
    compose_db: tuple[sessionmaker[Session], str],
) -> None:
    sessions, container_url = compose_db
    seed(sessions)
    with sessions.begin() as session:
        north = session.scalars(select(Team).where(Team.slug == "north")).one()
        user = accounts.create_user(
            session, north.org_id, "owner@larkspur.example", "Owner", [], PASSWORD
        )
        accounts.set_memberships(session, user, {north.id: "salesperson"}, FakeClock())
    stored = _store(sessions, {"name": "Dana Draft", "listing": "Alder Ridge"})

    run = uuid.uuid4().hex[:8]
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    no_key = {"FIRSTCALL_MODEL_MODE": "live", "FIRSTCALL_MODEL_NAME": MODEL}
    names = [f"firstcall-suggest-worker-{run}", f"firstcall-suggest-api-{run}"]
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
        with sessions() as session:
            lead_id = session.get_one(Message, stored).lead_id
        path = f"/leads/{lead_id}"
        text = _page_note()

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            owner = browser.new_context(base_url=base).new_page()
            _signed_in(owner, "owner@larkspur.example")
            owner.get_by_role("button", name="Claim Dana Draft").click()
            expect(owner.get_by_text("You own this lead now.").first).to_be_visible()
            owner.goto(path)
            owner.get_by_label("Add a note").fill(text)
            owner.get_by_role("button", name="Save note").click()
            expect(owner.get_by_text("Note saved.").first).to_be_visible()

            def waiting() -> bool:
                with sessions() as session:
                    note = session.scalars(select(Note)).one()
                    return note.read_state == "needs_model"

            _wait_for(waiting, "the no-key worker to leave the note waiting")
            owner.reload()
            expect(owner.get_by_text("Needs a model key")).to_be_visible()

            with sessions.begin() as session:
                # 09:00 in New York on the unit notes' date, the day the answer was recorded.
                dated = datetime.fromisoformat(UNIT_DATE).replace(hour=14, tzinfo=UTC)
                session.execute(update(Note).values(created_at=dated))
                # The buyer's message took the rules path with no key; its reread is not what
                # this test is about, and replay has no recording for it.
                session.execute(update(Message).values(needs_reread=False))
            replay = {
                "FIRSTCALL_MODEL_MODE": "replay",
                "FIRSTCALL_MODEL_NAME": MODEL,
                "FIRSTCALL_MODEL_RECORDINGS": "/fixtures",
            }
            _run(f"firstcall-suggest-tick-{run}", container_url, "worker", ["tick"], replay,
                 volume=f"{RECORDINGS}:/fixtures:ro", detach=False)  # fmt: skip

            owner.reload()
            expect(owner.get_by_text("Needs a model key")).to_have_count(0)
            expect(owner.get_by_text(f"Tidied: {CLEAN}")).to_be_visible()
            expect(
                owner.get_by_text("Suggested: Called — “Called Dana this morning”")
            ).to_be_visible()
            owner.get_by_role("button", name="Confirm called").click()
            expect(owner.get_by_text("Contact logged.").first).to_be_visible()
            expect(owner.get_by_text("Called · contact logged")).to_be_visible()
            expect(owner.get_by_text("confirmed", exact=True)).to_be_visible()
            browser.close()

        with sessions() as session:
            [contact] = session.scalars(select(Contact)).all()
            assert (contact.kind, contact.source) == ("call", "suggestion")
            assert session.scalars(select(NoteSuggestion.state)).all() == ["confirmed"]
    finally:
        for name in names:
            subprocess.run(["docker", "stop", name], capture_output=True, timeout=60)
