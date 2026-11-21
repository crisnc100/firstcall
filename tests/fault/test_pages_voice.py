"""Voice memos in a real browser on compose, with no model key and no reload (F40, first
half). The owner picks Called and sends a memo Chromium recorded, sees "Transcribing…" turn
into the transcript as a Voice note with the call logged, then "Needs a model key". A
transcript holding a script tag shows as text. A memo recorded on the page with Record and
Stop goes up, and when it cannot be transcribed the note says to record it again.

The transcriber runs from the app image in replay mode: transcripts come from
tests/fixtures/voice/page-transcripts.jsonl by the audio's hash, so no model is downloaded.
"""

from __future__ import annotations

import re
import subprocess
import uuid
from pathlib import Path

import httpx
from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.db import Message, Note, Team
from firstcall.seed import seed
from tests.conftest import FakeClock
from tests.fault.test_pages import PASSWORD, _free_port, _store, _wait_for
from tests.fault.test_pages import compose_db as compose_db  # a fixture, found by name
from tests.fault.test_pages_suggestions import ALL_INTERFACES, MODEL, _run, _signed_in

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "voice"
RECORDED = FIXTURES / "unit-page.webm"
# small.en's transcript of the recording, made once in the voice image (builds/pr4/log.md).
TRANSCRIPT = (
    "Call Dana this morning, budget is around $450,000, once three bedrooms and a garage, "
    "hoping to move by June."
)
SCRIPT_TRANSCRIPT = "<script>window.__pwned = 1</script>Left a voicemail."
# Lead pages poll every 15 seconds while a note waits.
POLL_WAIT_MS = 25_000
# The page records a tone in place of a microphone: Chromium's fake device hangs on macOS,
# and the recording is what is under test, not the device.
FAKE_MICROPHONE = """
window.__micDelay = 0;
navigator.mediaDevices.getUserMedia = async () => {
  await new Promise((resolve) => setTimeout(resolve, window.__micDelay));
  const context = new AudioContext();
  const tone = context.createOscillator();
  const out = context.createMediaStreamDestination();
  tone.connect(out);
  tone.start();
  window.__microphone = out.stream;
  return out.stream;
};
"""
TYPED = "Typed while recording."
TYPED_WHILE_ASKED = "Typed while the browser asked for the microphone."
MIC_ENDED = "() => window.__microphone?.getTracks().every((t) => t.readyState === 'ended')"


def _notes(page: Page) -> list[str]:
    return page.locator("ul.notes > li").all_inner_texts()


def test_a_voice_memo_becomes_a_note_in_a_browser(
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
    stored = _store(sessions, {"name": "Vera Voice", "listing": "Alder Ridge"})

    run = uuid.uuid4().hex[:8]
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    no_key = {"FIRSTCALL_MODEL_MODE": "live", "FIRSTCALL_MODEL_NAME": MODEL}
    names = [f"firstcall-voice-worker-{run}", f"firstcall-voice-api-{run}"]
    transcriber = f"firstcall-voice-transcriber-{run}"
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
        assert lead_id is not None

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                args=["--autoplay-policy=no-user-gesture-required"]
            )
            context = browser.new_context(base_url=base)
            context.add_init_script(FAKE_MICROPHONE)
            page = context.new_page()
            _signed_in(page, "owner@larkspur.example")
            page.get_by_role("button", name="Claim Vera Voice").click()
            expect(page.get_by_text("You own this lead now.").first).to_be_visible()
            page.goto(f"/leads/{lead_id}")
            expect(page.get_by_role("heading", name="Notes (0)")).to_be_visible()
            # Any reload would clear this.
            page.evaluate("() => { window.__stayed = true; }")

            # No transcriber runs yet, so the memo waits where the page can see it.
            page.get_by_label("This note records").select_option(label="Called")
            page.get_by_label("Send a recording").set_input_files(RECORDED)
            expect(page.get_by_text("Memo sent and contact logged.").first).to_be_visible()
            memo = page.locator("ul.notes > li").first
            expect(memo.get_by_text("Voice", exact=True)).to_be_visible()
            expect(memo.get_by_text("Transcribing…")).to_be_visible()
            expect(memo.get_by_text("Called · contact logged")).to_be_visible()
            expect(page.get_by_text("Called: logged by Owner")).to_be_visible()

            _run(transcriber, container_url, "worker", ["worker", "--kind", "transcribe"],
                 {"FIRSTCALL_TRANSCRIBE_MODE": "replay",
                  "FIRSTCALL_VOICE_TRANSCRIPTS": "/fixtures/page-transcripts.jsonl",
                  # A memo with no recorded transcript fails at once, not after 30 seconds
                  # of retries.
                  "FIRSTCALL_MAX_ATTEMPTS": "1"},
                 volume=f"{FIXTURES}:/fixtures:ro")  # fmt: skip
            names.append(transcriber)
            expect(memo.get_by_text(TRANSCRIPT)).to_be_visible(timeout=POLL_WAIT_MS)
            expect(memo.get_by_text("Needs a model key")).to_be_visible(timeout=POLL_WAIT_MS)
            expect(memo.get_by_text("Transcribing…")).to_have_count(0)
            expect(memo.get_by_text("Called · contact logged")).to_be_visible()

            page.get_by_label("Send a recording").set_input_files(FIXTURES / "ok.webm")
            expect(page.get_by_text("Memo sent. Transcribing…").first).to_be_visible()
            expect(page.get_by_text(SCRIPT_TRANSCRIPT)).to_be_visible(timeout=POLL_WAIT_MS)
            assert page.evaluate("() => window.__pwned") is None

            page.get_by_role("button", name="Record a memo").click()
            stop = page.get_by_role("button", name="Stop and send")
            expect(stop).to_be_focused()
            expect(page.locator(".memo-timer")).to_have_text(re.compile(r"0:0[1-9] / 3:00"))
            stop.click()
            expect(page.get_by_text("Memo sent. Transcribing…").first).to_be_visible()
            expect(page.get_by_role("heading", name="Notes (3)")).to_be_visible()
            expect(
                page.get_by_text("Couldn't transcribe this memo. Record it again.")
            ).to_be_visible(timeout=POLL_WAIT_MS)

            # Saving a typed note mid-memo redraws the page: the memo is dropped, unsent,
            # and the microphone let go.
            page.get_by_role("button", name="Record a memo").click()
            expect(page.locator(".memo-timer")).to_have_text(re.compile(r"0:0[1-9] / 3:00"))
            page.get_by_label("Add a note").fill(TYPED)
            page.get_by_role("button", name="Save note").click()
            expect(page.get_by_text("Note saved.").first).to_be_visible()
            page.wait_for_function(MIC_ENDED)
            page.wait_for_timeout(1500)
            expect(page.get_by_role("heading", name="Notes (4)")).to_be_visible()

            # A redraw while the browser asks for the microphone: nothing records once it
            # answers, and the microphone is let go.
            page.evaluate("() => { window.__micDelay = 1500; window.__microphone = null; }")
            page.get_by_role("button", name="Record a memo").click()
            page.get_by_label("Add a note").fill(TYPED_WHILE_ASKED)
            page.get_by_role("button", name="Save note").click()
            expect(page.get_by_text("Note saved.").first).to_be_visible()
            page.wait_for_function(MIC_ENDED)
            expect(page.get_by_role("button", name="Stop and send")).to_have_count(0)
            page.wait_for_timeout(1000)
            expect(page.get_by_role("heading", name="Notes (5)")).to_be_visible()
            page.evaluate("() => { window.__micDelay = 0; }")

            # A recorder that cannot start lets go of the microphone and resets the form.
            page.evaluate(
                "() => { window.__start = MediaRecorder.prototype.start;"
                " window.__microphone = null;"
                " MediaRecorder.prototype.start = () => { throw new Error('no recorder'); }; }"
            )
            page.get_by_role("button", name="Record a memo").click()
            expect(
                page.locator(".danger-text").get_by_text(
                    "This browser couldn't record. You can still send a recording."
                )
            ).to_be_visible()
            page.wait_for_function(MIC_ENDED)
            expect(page.get_by_role("button", name="Record a memo")).to_be_enabled()
            expect(page.get_by_role("button", name="Stop and send")).to_have_count(0)

            # A recorder that fails mid-memo sends nothing.
            page.evaluate(
                "() => { window.__microphone = null;"
                " MediaRecorder.prototype.start = function (...args) {"
                "   window.__start.apply(this, args);"
                "   setTimeout(() => {"
                "     this.dispatchEvent(new Event('error'));"
                "     this.stop();"
                "   }, 800);"
                " }; }"
            )
            page.get_by_role("button", name="Record a memo").click()
            expect(
                page.locator(".danger-text").get_by_text(
                    "The recording failed. Record it again, or send a recording."
                )
            ).to_be_visible()
            page.wait_for_function(MIC_ENDED)
            page.evaluate("() => { MediaRecorder.prototype.start = window.__start; }")
            page.wait_for_timeout(1000)
            expect(page.get_by_role("heading", name="Notes (5)")).to_be_visible()

            # A picked file the browser gives no type is sent with its extension's.
            page.get_by_label("Send a recording").set_input_files(
                {
                    "name": "memo.m4a",
                    "mimeType": "",
                    "buffer": (FIXTURES / "moov-last.m4a").read_bytes(),
                }
            )
            expect(page.get_by_text("Memo sent. Transcribing…").first).to_be_visible()
            expect(page.get_by_role("heading", name="Notes (6)")).to_be_visible()
            assert page.evaluate("() => window.__stayed") is True
            browser.close()

        def settled() -> bool:
            with sessions() as session:
                states = session.scalars(select(Note.read_state)).all()
                return "pending" not in states and "transcribing" not in states

        _wait_for(settled, "the typed note's read")
        with sessions() as session:
            found = sorted((n.text, n.source, n.read_state, n.read_reason or "")
                           for n in session.scalars(select(Note)))  # fmt: skip
        assert found == sorted([
            (TRANSCRIPT, "voice", "needs_model", "no_key"),
            (SCRIPT_TRANSCRIPT, "voice", "needs_model", "no_key"),
            ("", "voice", "failed", "not_transcribed"),
            ("", "voice", "failed", "not_transcribed"),
            (TYPED, "typed", "needs_model", "no_key"),
            (TYPED_WHILE_ASKED, "typed", "needs_model", "no_key"),
        ])  # fmt: skip
    finally:
        for name in names:
            subprocess.run(["docker", "stop", name], capture_output=True, timeout=60)
