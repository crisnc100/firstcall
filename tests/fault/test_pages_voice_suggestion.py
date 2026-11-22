"""A voice memo to a confirmed contact in a real browser on compose, with no reload (F40,
second half). The owner sends a memo picking no contact, sees "Transcribing…" turn into the
transcript, then the call the note reader suggests, confirms it and sees the contact logged.

The memo is a seed-11 clip of a completed call. The transcriber replays the shipped size's
committed transcript of it (evals/voice/transcripts), and the worker replays the note
reader's answer recorded in the voice eval (evals/recordings), so no model or key is used.
The answer was recorded at the script's note date in New York, so the test dates the memo to
that day before the transcriber starts, as the suggestions page test does.
"""

from __future__ import annotations

import hashlib
import subprocess
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.corpus.notes import SCRIPTS, read_jsonl
from firstcall.corpus.voice import VOICE_DIR, read_voice_manifest
from firstcall.db import Contact, Message, Note, NoteSuggestion, Team
from firstcall.evaluation.note_eval import RECORDINGS
from firstcall.evaluation.voice_eval import VOICE_RUNS, read_transcripts, transcripts_path
from firstcall.seed import seed
from firstcall.settings import Settings
from tests.conftest import FakeClock
from tests.fault.test_pages import PASSWORD, _free_port, _store, _wait_for
from tests.fault.test_pages import compose_db as compose_db  # a fixture, found by name
from tests.fault.test_pages_suggestions import ALL_INTERFACES, MODEL, _run, _signed_in
from tests.fault.test_pages_voice import POLL_WAIT_MS

SCRIPT = "note-11-141"
CLIP = VOICE_DIR / "seed-11" / "clean" / f"{SCRIPT}.mp3"
SHIPPED = Settings.model_fields["whisper_size"].default
TRANSCRIPTS = transcripts_path(VOICE_RUNS, SHIPPED, "clean")
QUOTE = "called Susan this morning"


def _transcript() -> str:
    return next(row["text"] for row in read_transcripts(TRANSCRIPTS) if row["id"] == SCRIPT)


def _note_date() -> str:
    return next(row["note_date"] for row in read_jsonl(11, SCRIPTS) if row["id"] == SCRIPT)


def test_a_voice_memo_leads_to_a_confirmed_contact_in_a_browser(
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
    stored = _store(sessions, {"name": "Susan Speaks", "listing": "Alder Ridge"})

    run = uuid.uuid4().hex[:8]
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    no_key = {"FIRSTCALL_MODEL_MODE": "live", "FIRSTCALL_MODEL_NAME": MODEL}
    first_worker = f"firstcall-memo-worker-{run}"
    # Each name is kept before its container starts, so a launch that fails partway is
    # stopped too.
    names: list[str] = []

    def launch(name: str, *args: Any, **kwargs: Any) -> None:
        names.append(name)
        _run(name, *args, **kwargs)

    try:
        launch(first_worker, container_url, "worker", ["worker"], no_key)
        launch(f"firstcall-memo-api-{run}", container_url, "api",
               ["api", "--host", ALL_INTERFACES, "--port", "8000"], {}, port=port)  # fmt: skip

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
        # The buyer's message took the rules path with no key. The note's read needs the
        # recorded answer, so a worker in replay mode takes over before the memo arrives.
        subprocess.run(["docker", "stop", first_worker], capture_output=True, timeout=60)
        launch(f"firstcall-memo-replay-worker-{run}", container_url, "worker", ["worker"],
               {"FIRSTCALL_MODEL_MODE": "replay", "FIRSTCALL_MODEL_NAME": MODEL,
                "FIRSTCALL_MODEL_RECORDINGS": "/recordings"},
               volume=f"{RECORDINGS}:/recordings:ro")  # fmt: skip

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_context(base_url=base).new_page()
            _signed_in(page, "owner@larkspur.example")
            page.get_by_role("button", name="Claim Susan Speaks").click()
            expect(page.get_by_text("You own this lead now.").first).to_be_visible()
            page.goto(f"/leads/{lead_id}")
            expect(page.get_by_role("heading", name="Notes (0)")).to_be_visible()
            # Any reload would clear this.
            page.evaluate("() => { window.__stayed = true; }")

            page.get_by_label("Send a recording").set_input_files(CLIP)
            expect(page.get_by_text("Memo sent. Transcribing…").first).to_be_visible()
            memo = page.locator("ul.notes > li").first
            expect(memo.get_by_text("Voice", exact=True)).to_be_visible()
            expect(memo.get_by_text("Transcribing…")).to_be_visible()

            with sessions.begin() as session:
                # 10:00 in New York on the script's date, the day its answer was recorded.
                dated = datetime.fromisoformat(_note_date()).replace(hour=14, tzinfo=UTC)
                session.execute(update(Note).values(created_at=dated))
            launch(f"firstcall-memo-transcriber-{run}", container_url, "worker",
                   ["worker", "--kind", "transcribe"],
                   {"FIRSTCALL_TRANSCRIBE_MODE": "replay",
                    "FIRSTCALL_VOICE_TRANSCRIPTS": f"/transcripts/{TRANSCRIPTS.name}"},
                   volume=f"{TRANSCRIPTS.parent}:/transcripts:ro")  # fmt: skip

            expect(memo.get_by_text(_transcript())).to_be_visible(timeout=POLL_WAIT_MS)
            suggested = memo.get_by_text(f"Suggested: Called — “{QUOTE}”")
            expect(suggested).to_be_visible(timeout=POLL_WAIT_MS)
            memo.get_by_role("button", name="Confirm called").click()
            expect(page.get_by_text("Contact logged.").first).to_be_visible()
            expect(memo.get_by_text("Called · contact logged")).to_be_visible()
            expect(memo.get_by_text("confirmed", exact=True)).to_be_visible()
            assert page.evaluate("() => window.__stayed") is True
            browser.close()

        with sessions() as session:
            [note] = session.scalars(select(Note)).all()
            assert (note.source, note.text, note.read_state) == ("voice", _transcript(), "read")
            [contact] = session.scalars(select(Contact)).all()
            assert (contact.kind, contact.source) == ("call", "suggestion")
            assert session.scalars(select(NoteSuggestion.state)).all() == ["confirmed"]
    finally:
        # A container that never started is already gone, and its stop fails quietly.
        for name in names:
            subprocess.run(["docker", "stop", name], capture_output=True, timeout=60)


def test_the_memo_is_the_shipped_sizes_transcript_of_a_call() -> None:
    """The clip, its committed transcript and the quote the test waits for agree, so a gold or
    transcript change fails here rather than as a page that never shows a suggestion."""
    row = next(row for row in read_transcripts(TRANSCRIPTS) if row["id"] == SCRIPT)
    assert row["sha256"] == hashlib.sha256(CLIP.read_bytes()).hexdigest()
    assert row["sha256"] == read_voice_manifest()[f"seed-11/clean/{SCRIPT}.mp3"]
    assert QUOTE in row["text"]
