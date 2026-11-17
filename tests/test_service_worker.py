"""The service worker's notification click opens only this app's own pages. sw.js runs in a
browser page with a stand-in for the worker's `self`, so the real file is what is tested."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright

SW = Path(__file__).resolve().parents[1] / "web" / "public" / "sw.js"
ORIGIN = "https://firstcall.example"

# Runs sw.js against a fake worker scope, fires one notification click and returns the
# address the worker navigated an open window to.
CLICK = """([source, origin, url, hasWindow]) => {
  const listeners = {};
  const opened = [];
  const window = { focus: () => Promise.resolve(), navigate: (to) => opened.push(to) };
  const self = {
    location: { origin },
    addEventListener: (name, handler) => { listeners[name] = handler; },
    registration: { showNotification: () => Promise.resolve() },
    clients: {
      matchAll: () => Promise.resolve(hasWindow ? [window] : []),
      openWindow: (to) => { opened.push(to); return Promise.resolve(); },
    },
  };
  new Function("self", source)(self);
  let done;
  listeners.notificationclick({
    notification: { close: () => {}, data: url === null ? undefined : { url } },
    waitUntil: (promise) => { done = promise; },
  });
  return done.then(() => opened);
}"""


@pytest.fixture(scope="module")
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        opened = browser.new_page()
        yield opened
        browser.close()


def _opens(page: Page, url: str | None, has_window: bool = True) -> list[str]:
    return page.evaluate(CLICK, [SW.read_text(), ORIGIN, url, has_window])


def test_a_lead_link_opens_that_page(page: Page) -> None:
    assert _opens(page, f"{ORIGIN}/leads/42") == ["/leads/42"]
    assert _opens(page, "/leads/42", has_window=False) == ["/leads/42"]


@pytest.mark.parametrize(
    "url",
    [
        f"{ORIGIN}//evil.example/x",
        f"{ORIGIN}/\\evil.example/x",
        "https://evil.example/leads/42",
        "//evil.example/leads/42",
    ],
)
def test_another_host_or_a_double_slash_path_opens_the_fallback(page: Page, url: str) -> None:
    assert _opens(page, url) == ["/mine"]
    assert _opens(page, url, has_window=False) == ["/mine"]


def test_no_url_opens_the_fallback_not_the_front_page(page: Page) -> None:
    assert _opens(page, None) == ["/mine"]
