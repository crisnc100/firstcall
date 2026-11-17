"""The public front page: its examples are the repo's own data, and its demo rail behaves like
the board. The browser checks run against the built pages in web/dist, served the way the api
serves them, with no database and no Docker; verify.sh builds the pages before pytest."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from playwright.sync_api import Browser, Page, expect, sync_playwright

from firstcall.api import WEB_DIST, serve_pages
from firstcall.core import escalation
from firstcall.core.note_read import answer_schema
from firstcall.evaluation.note_eval import ZONE
from firstcall.model.prompts import READ_NOTE, SYSTEM_PROMPTS, user_message
from firstcall.model.recordings import request_key
from firstcall.model.transport import TransportRequest
from firstcall.notes.reader import context_text

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "web" / "src" / "front" / "examples.json"
GOLD = ROOT / "evals" / "gold"
NOTE_RECORDINGS = ROOT / "evals" / "recordings" / "notes-claude-haiku-5-5.jsonl"
MODEL = "claude-haiku-5-5"
TICKET_IDS = ["s13-0006", "s13-0004", "s13-0058", "s13-0012"]
# Minutes each ticket has waited when the page opens, as web/src/front/main.ts sets them.
STARTS = [0.0, 4 + 50 / 60, 14 + 45 / 60, 62 + 10 / 60]


def _rows(path: Path) -> dict[str, dict[str, Any]]:
    rows = (json.loads(line) for line in path.read_text().splitlines() if line.strip())
    return {row["id"]: row for row in rows}


def _examples() -> dict[str, Any]:
    return json.loads(EXAMPLES.read_text())


def _cut_at_signature(source: str) -> tuple[str, list[str]]:
    """The source email's From and Subject headers and its body up to the signature, exactly,
    and the signature's lines. The signature is found from the email itself: the body's last
    paragraph, which must open with the sign-off."""
    head, _, body = source.partition("\n\n")
    headers = [line for line in head.split("\n") if line.startswith(("From:", "Subject:"))]
    assert [line.split(":", 1)[0] for line in headers] == ["From", "Subject"]
    paragraphs = body.rstrip("\n").split("\n\n")
    signature = paragraphs[-1].split("\n")
    assert signature[0] == "Best regards,"
    kept = "\n".join(headers) + "\n\n" + "\n\n".join(paragraphs[:-1])
    return kept, signature


def test_the_tickets_are_seed_13_messages_and_their_labels_verbatim() -> None:
    messages = _rows(GOLD / "seed-13" / "messages.jsonl")
    labels = _rows(GOLD / "seed-13" / "labels.jsonl")
    tickets = _examples()["tickets"]
    assert [t["message"]["id"] for t in tickets] == TICKET_IDS
    for ticket in tickets:
        assert ticket["message"] == messages[ticket["message"]["id"]]
        assert ticket["labels"] == labels[ticket["message"]["id"]]
    # The first ticket is the agent's email: two phone numbers, and the buyer's is printed.
    first = tickets[0]["labels"]
    assert first["agent"]["phone"] != first["phone"]
    digits = "".join(ch for ch in tickets[0]["message"]["body"] if ch.isdigit())
    assert first["phone"][2:] in digits
    assert first["agent"]["phone"][2:] in digits


def test_the_note_is_a_held_out_script_and_the_readers_recorded_answer() -> None:
    note = _examples()["note"]
    script = note["script"]
    assert script == _rows(GOLD / "notes" / "seed-11" / "scripts.jsonl")[script["id"]]
    assert note["labels"] == _rows(GOLD / "notes" / "seed-11" / "labels.jsonl")[script["id"]]
    request = TransportRequest(
        model=MODEL,
        system=SYSTEM_PROMPTS[READ_NOTE],
        user=user_message(context_text(script["note_date"], ZONE), script["text"], READ_NOTE),
        schema=answer_schema(),
        max_output_tokens=1,
        timeout_seconds=1,
    )
    recorded = _rows_by_key(NOTE_RECORDINGS)[request_key(request)]
    assert note["recording_key"] == recorded["key"]
    assert note["answer"] == json.loads(recorded["content"])
    # The page says the future-tense call gets no suggestion; the recorded answer agrees.
    assert "Will call Charles tomorrow." in script["text"]
    assert all("Will call" not in action["quote"] for action in note["answer"]["actions"])
    assert [a["kind"] for a in note["answer"]["actions"]] == note["labels"]["actions"]


def _rows_by_key(path: Path) -> dict[str, dict[str, Any]]:
    rows = (json.loads(line) for line in path.read_text().splitlines() if line.strip())
    return {row["key"]: row for row in rows}


def test_the_late_labels_use_the_escalation_minutes_the_board_uses() -> None:
    # web/src/format.ts falls back to these when a lead has no team figures, and the demo
    # rail uses that fallback.
    assert escalation.DEFAULTS[escalation.TEAM_LEADS] == 15
    assert escalation.DEFAULTS[escalation.ADMINS] == 60


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    assert (WEB_DIST / "index.html").is_file(), "build the pages first: cd web && npm run build"
    app = FastAPI()
    serve_pages(app, WEB_DIST)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "the page server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch()
        yield launched
        launched.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    opened = context.new_page()
    yield opened
    context.close()


def test_the_first_ticket_shows_the_source_message_and_its_fields(
    page: Page, base_url: str
) -> None:
    page.goto(base_url + "/")
    ticket = _examples()["tickets"][0]
    labels = ticket["labels"]
    first = page.locator('li.ticket[data-id="s13-0006"]')
    body = ticket["message"]["body"]
    shown = first.locator("pre.raw").text_content() or ""
    kept, signature = _cut_at_signature(body)
    assert shown == kept
    # No signature line is shown. A signature line may still occur inside a kept line (the
    # agent's name and address are in the From header), but only where the kept source text
    # has it too.
    for line in signature:
        assert line not in shown.split("\n")
        assert line not in shown or line in kept
    expect(first.locator(".t-cut")).to_have_text("Signature not shown")
    printed = dict(
        zip(
            first.locator(".fields dt").all_text_contents(),
            first.locator(".fields dd").all_text_contents(),
            strict=True,
        )
    )
    assert printed == {
        "Buyer": labels["name"],
        "Phone": labels["phone"],
        "Email": labels["email"],
        "Wants": labels["item"],
        "Agent": f"{labels['agent']['name']} {labels['agent']['phone']}",
        "Team": labels["team"],
    }
    note = _examples()["note"]
    pair = page.locator("#note-pair")
    expect(pair.locator(".slip.typed .print")).to_have_text(note["script"]["text"])
    expect(pair.locator(".slip.read > .print").first).to_have_text(note["answer"]["clean_note"])


def test_heard_claims_the_first_ticket_at_once(page: Page, base_url: str) -> None:
    page.goto(base_url + "/")
    first = page.locator('li.ticket[data-id="s13-0006"]')
    first.get_by_role("button", name="Heard: claim Amanda Lewis's lead").click()
    expect(first).to_have_attribute("data-age", "claimed")
    expect(first.locator(".claimed")).to_contain_text("Claimed by you")
    expect(first.locator(".claimed")).to_be_focused()
    expect(first.get_by_role("button")).to_have_count(0)
    expect(page.locator("#claim-status")).to_contain_text("nothing was sent to the buyer")
    # The other tickets are untouched.
    expect(page.locator("li.ticket .heard")).to_have_count(3)


def test_the_age_labels_change_at_the_boards_thresholds(page: Page, base_url: str) -> None:
    page.clock.install()
    page.goto(base_url + "/")

    def word(index: int) -> str:
        return page.locator("li.ticket .age-word").nth(index).inner_text().strip().lower()

    assert [word(i) for i in range(4)] == ["new", "new", "waiting", "very late"]
    # 4:50 and 14:45 at open: 10 seconds on, the second ticket reaches 5 minutes.
    page.clock.run_for(9_000)
    assert word(1) == "new"
    page.clock.run_for(2_000)
    assert word(1) == "waiting"
    page.clock.run_for(4_000)  # 15 s in: the third ticket reaches 15 minutes
    assert word(2) == "late"
    assert page.locator("li.ticket").nth(2).get_attribute("data-age") == "late"
    page.clock.run_for(5 * 60_000 - 15_000)  # the first ticket at 5:00
    assert word(0) == "waiting"
    page.clock.run_for(10 * 60_000)  # 15:00
    assert word(0) == "late"
    page.clock.run_for(45 * 60_000 - 2_000)  # 59:58
    assert word(0) == "late"
    page.clock.run_for(3_000)  # 60:01
    assert word(0) == "very late"


def test_nothing_scrolls_sideways_at_390(browser: Browser, base_url: str) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 844})
    narrow = context.new_page()
    narrow.goto(base_url + "/")
    assert narrow.evaluate("document.documentElement.scrollWidth") <= 390
    boxes = narrow.evaluate(
        "[...document.querySelectorAll('li.ticket, .slip')].map((el) => "
        "el.getBoundingClientRect().right)"
    )
    assert boxes
    assert max(boxes) <= 390
    context.close()
