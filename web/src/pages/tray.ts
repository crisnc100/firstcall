// The Not-a-lead tray and one tray message, with the rescue.

import { api, ApiError, type MessageRow } from "../api";
import { announce, h, link } from "../dom";
import { fullTime, shortTime } from "../format";
import { icon } from "../icons";
import { busy, unbusy } from "../leadlist";
import { emptyState, loadFailed, poll, skeleton, staleLine } from "../poller";
import type { Cleanup } from "../router";
import { setTitle } from "../shell";
import { categoryLabel, sourceLabel } from "../labels";
import { messageText } from "./detail";


export function trayPage(main: HTMLElement): Cleanup {
  setTitle("Not-a-lead tray");
  let filter = "all";
  let rows: MessageRow[] = [];
  const tabs = h("nav", { class: "teamtabs viewtabs", attrs: { "aria-label": "Category" } });
  const stale = staleLine();
  const body = h("div", {}, skeleton());
  main.replaceChildren(
    h("h1", { text: "Not-a-lead tray", attrs: { tabindex: "-1" } }),
    h("p", {
      class: "muted lede",
      text: "Messages the rules read as vendor, support or spam. Move any real lead back to the leads.",
    }),
    tabs,
    stale,
    body,
  );
  const draw = (): void => {
    const counts: Record<string, number> = { all: rows.length };
    for (const row of rows) counts[row.category ?? ""] = (counts[row.category ?? ""] ?? 0) + 1;
    tabs.replaceChildren(
      ...["all", "vendor", "support", "spam"].map((key) => {
        const anchor = h("a", {
          text: `${key === "all" ? "All" : categoryLabel(key)} ${counts[key] ?? 0}`,
          attrs: { href: "#", "aria-current": key === filter ? "page" : undefined },
        });
        anchor.addEventListener("click", (event) => {
          event.preventDefault();
          filter = key;
          draw();
        });
        return anchor;
      }),
    );
    const shown = rows.filter((row) => filter === "all" || row.category === filter);
    if (!rows.length) {
      body.replaceChildren(emptyState("The tray is empty.", "Nothing has been filed as vendor, support or spam."));
      return;
    }
    body.replaceChildren(
      h(
        "ul",
        { class: "list-plain", attrs: { "aria-label": `Not-a-lead tray, ${shown.length} messages` } },
        ...shown.map((row) =>
          h(
            "li",
            {},
            h("span", { class: "badge", text: categoryLabel(row.category) }),
            link(`/tray/${row.id}`, row.name ?? row.email ?? "No sender name", "name"),
            h("span", { class: "muted sm num", text: `${shortTime(row.received_at)} · ${sourceLabel(row.source)}` }),
            h("span", { class: "muted sm", text: row.reason ?? "" }),
          ),
        ),
      ),
    );
  };
  let loaded = false;
  const load = async (): Promise<void> => {
    try {
      rows = await api<MessageRow[]>("GET", "/tray");
      loaded = true;
      draw();
    } catch (error) {
      if (!loaded) body.replaceChildren(loadFailed("the tray", () => void poller.now()));
      throw error;
    }
  };
  const poller = poll(load, stale);
  void poller.now();
  return poller.stop;
}

export function trayMessagePage(main: HTMLElement, params: Record<string, string>): Cleanup {
  setTitle("Tray message");
  main.replaceChildren(skeleton());
  void api<MessageRow>("GET", `/tray/${params.id}`)
    .then((row) => draw(main, row))
    .catch((error: unknown) => {
      if (error instanceof ApiError && error.status === 404) {
        main.replaceChildren(
          h("h1", { text: "Message not in the tray" }),
          h("p", { text: "It may have been moved to the leads already." }),
          link("/tray", "Back to the tray"),
        );
        return;
      }
      main.replaceChildren(loadFailed("the message", () => trayMessagePage(main, params)));
    });
  return () => undefined;
}

function draw(main: HTMLElement, row: MessageRow): void {
  const status = h("div", { attrs: { role: "status" } });
  const rescue = h("button", {
    class: "btn primary",
    text: "This is a lead: move it to the leads",
    attrs: { type: "button" },
  });
  rescue.addEventListener("click", () => {
    busy(rescue, "Moving…");
    void api<{ status: string; lead_id: string }>("POST", `/tray/${row.id}/rescue`)
      .then((answer) => {
        const text = "Moved to the leads. It's waiting in its pool.";
        const result = h(
          "p",
          { class: "line", attrs: { tabindex: "-1" } },
          icon("check", true),
          text,
          link(`/leads/${answer.lead_id}?from=unassigned`, "Open the lead"),
        );
        status.replaceChildren(result);
        // Removing the focused button would drop focus to the page body.
        result.focus();
        rescue.remove();
        announce(text);
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 409) {
          const result = h("p", { text: "Already moved to the leads.", attrs: { tabindex: "-1" } });
          status.replaceChildren(result);
          result.focus();
          rescue.remove();
          announce("Already moved to the leads.");
          return;
        }
        unbusy(rescue, "This is a lead: move it to the leads");
        status.replaceChildren(
          h("p", { class: "danger-text" }, icon("alert"), "Couldn't move it. Check your connection and try again."),
        );
      });
  });
  setTitle(row.name ?? row.email ?? "Tray message");
  main.replaceChildren(
    link("/tray", "Back to the tray", "sm"),
    h("h1", { class: "lead-name", text: row.name ?? row.email ?? "No sender name" }),
    h(
      "dl",
      {},
      h("dt", { text: "From" }),
      h("dd", { text: row.name ?? "No name given" }),
      h("dt", { text: "Phone" }),
      h("dd", { class: "num", text: row.phone ?? "No phone given" }),
      h("dt", { text: "Email" }),
      h("dd", { text: row.email ?? "No email given" }),
      h("dt", { text: "Source" }),
      h("dd", { text: sourceLabel(row.source) }),
      h("dt", { text: "Received" }),
      h("dd", { class: "num", text: fullTime(row.received_at) }),
      h("dt", { text: "Category" }),
      h("dd", { text: categoryLabel(row.category) }),
      h("dt", { text: "Reason" }),
      h("dd", { text: row.reason ?? "" }),
    ),
    messageText(row.text),
    rescue,
    status,
  );
}
