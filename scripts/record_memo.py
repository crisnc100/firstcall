"""Record a voice memo the way a person does, and keep the file the page sends.

Chromium's fake microphone plays a WAV file into the lead page's Record button, and the
WebM the page uploads is saved. This made tests/fixtures/voice/unit-page.webm from a Piper
reading of the unit-page note (evals/gold/unit-notes.jsonl), so the page test sends what a
browser really records.

It needs the board running and a person who may note the lead, for example an admin on the
compose stack (docs/board.md). The password comes from FIRSTCALL_RECORD_PASSWORD, or is
asked for. The upload goes through, so the lead gets the memo as a voice note. On macOS
Chromium's fake microphone never answers, so run it on Linux; builds/pr4/log.md shows a
run inside the app image.

    uv run python -m scripts.record_memo --base http://localhost:8020 \\
        --email you@example.com --lead <lead id> --wav reading.wav --out memo.webm
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import re
import wave
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

# Time to stop after the reading ends, so its last word is not cut.
TAIL_SECONDS = 1.0
# Chromium does not report a Blob request body to Playwright, so the page keeps each Blob it
# sends, and the script reads the last one back.
KEEP_SENT = """
const send = window.fetch;
window.__sent = [];
window.fetch = (input, init) => {
  if (init && init.body instanceof Blob) window.__sent.push(init.body);
  return send(input, init);
};
"""
LAST_SENT = """async () => {
  const bytes = new Uint8Array(await window.__sent.at(-1).arrayBuffer());
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text);
}"""


def wav_seconds(wav: Path) -> float:
    with wave.open(str(wav)) as reading:
        return reading.getnframes() / reading.getframerate()


def record(base: str, email: str, password: str, lead: str, wav: Path) -> bytes:
    seconds = wav_seconds(wav)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                # %noloop: the reading plays once, then silence.
                f"--use-file-for-fake-audio-capture={wav.resolve()}%noloop",
            ]
        )
        context = browser.new_context(base_url=base, permissions=["microphone"])
        context.add_init_script(KEEP_SENT)
        page = context.new_page()
        page.goto("/signin")
        page.get_by_label("Email").fill(email)
        page.get_by_label("Password", exact=True).fill(password)
        page.get_by_role("button", name="Sign in").click()
        expect(page).not_to_have_url(re.compile(r"/signin"))
        page.goto(f"/leads/{lead}")
        page.get_by_role("button", name="Record a memo").click()
        stop = page.get_by_role("button", name="Stop and send")
        expect(stop).to_be_visible()
        page.wait_for_timeout((seconds + TAIL_SECONDS) * 1000)
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/voice" in r.url
        ) as sent:
            stop.click()
        if sent.value.status != 201:
            raise SystemExit(f"the board answered {sent.value.status}: {sent.value.text()}")
        body = base64.b64decode(page.evaluate(LAST_SENT))
        browser.close()
    return body


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a memo through the lead page.")
    parser.add_argument("--base", required=True, help="the board's address")
    parser.add_argument("--email", required=True)
    parser.add_argument("--lead", required=True, help="the lead's id")
    parser.add_argument("--wav", required=True, type=Path, help="what the microphone hears")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    password = os.environ.get("FIRSTCALL_RECORD_PASSWORD") or getpass.getpass()
    body = record(args.base, args.email, password, args.lead, args.wav)
    args.out.write_bytes(body)
    print(f"{args.out}: {len(body)} bytes")


if __name__ == "__main__":
    main()
