// Lists refresh every 15 seconds while the tab is visible, and at once when it becomes
// visible again. No data is pushed to the page, so this is how a pool stays current.

import { h } from "./dom";
import { shortTime } from "./format";

const EVERY_MS = 15000;

export interface Poll {
  stop: () => void;
  now: () => Promise<void>;
}

/** Run `load` now and on every refresh. The first failure is the caller's to show; a later
 * one shows a quiet "Not updated since" line that clears on the next success. */
export function poll(load: () => Promise<void>, stale: HTMLElement, tick?: () => void): Poll {
  let lastOk = "";
  let timer = 0;
  let ticker = 0;
  let stopped = false;
  const run = async (): Promise<void> => {
    if (stopped) return;
    try {
      await load();
      lastOk = new Date().toISOString();
      stale.hidden = true;
    } catch {
      if (lastOk) {
        stale.hidden = false;
        stale.textContent = `Not updated since ${shortTime(lastOk)}. Retrying.`;
      }
    }
  };
  const schedule = (): void => {
    window.clearInterval(timer);
    window.clearInterval(ticker);
    if (document.visibilityState !== "visible") return;
    timer = window.setInterval(() => void run(), EVERY_MS);
    if (tick) ticker = window.setInterval(tick, EVERY_MS);
  };
  const visibility = (): void => {
    if (document.visibilityState === "visible") void run();
    schedule();
  };
  document.addEventListener("visibilitychange", visibility);
  schedule();
  return {
    stop: () => {
      stopped = true;
      window.clearInterval(timer);
      window.clearInterval(ticker);
      document.removeEventListener("visibilitychange", visibility);
    },
    now: run,
  };
}

export function staleLine(): HTMLElement {
  return h("p", { class: "stale muted sm", attrs: { hidden: true } });
}

export function skeleton(): HTMLElement {
  return h(
    "div",
    { class: "leads", attrs: { "aria-hidden": "true" } },
    h("div", { class: "skeleton" }),
    h("div", { class: "skeleton" }),
    h("div", { class: "skeleton" }),
  );
}

export function emptyState(title: string, line: string, action?: HTMLElement): HTMLElement {
  return h("div", { class: "empty" }, h("h2", { text: title }), h("p", { class: "muted", text: line }), action ?? null);
}

export function loadFailed(what: string, retry: () => void): HTMLElement {
  return h(
    "div",
    { class: "empty", attrs: { role: "alert" } },
    h("h2", { text: `Couldn't load ${what}.` }),
    h("button", { class: "btn secondary", text: "Try again", attrs: { type: "button" }, on: { click: retry } }),
  );
}
