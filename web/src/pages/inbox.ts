// The in-app copies of alert emails. Only the plain-text body is shown: the HTML part
// carries buyer text and the app has no sanitiser for it.

import { api } from "../api";
import { h } from "../dom";
import { shortTime } from "../format";
import { emptyState, loadFailed, poll, skeleton, staleLine } from "../poller";
import type { Cleanup } from "../router";
import { setTitle, setUnread } from "../shell";

interface Mail {
  id: string;
  subject: string;
  text: string;
  created_at: string;
  read: boolean;
}

/** The body as text, with links to this app's own pages made clickable and nothing else. */
function body(text: string): HTMLElement {
  const block = h("div", { class: "msg" });
  const pattern = /https?:\/\/\S+/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const url = match[0];
    block.append(text.slice(last, match.index));
    let own: URL | null = null;
    try {
      own = new URL(url);
    } catch {
      own = null;
    }
    if (own && own.origin === window.location.origin) {
      block.append(h("a", { text: url, attrs: { href: own.pathname, "data-link": true } }));
    } else {
      block.append(url);
    }
    last = (match.index ?? 0) + url.length;
  }
  block.append(text.slice(last));
  return block;
}

export function inboxPage(main: HTMLElement): Cleanup {
  setTitle("Inbox");
  const count = h("span", { class: "muted num" });
  const stale = staleLine();
  const list = h("div", {}, skeleton());
  const reader = h("div", {});
  main.replaceChildren(h("div", { class: "head" }, h("h1", { text: "Inbox" }), count), stale, list, reader);
  let mails: Mail[] = [];
  let openId = "";
  const draw = (): void => {
    const unread = mails.filter((m) => !m.read).length;
    count.textContent = `${unread} unread`;
    setUnread(unread);
    if (!mails.length) {
      list.replaceChildren(emptyState("No alerts yet.", "Alerts about new and late leads also land here."));
      return;
    }
    list.replaceChildren(
      h(
        "ul",
        { class: "list-plain", attrs: { "aria-label": `Inbox, ${mails.length} alerts` } },
        ...mails.map((mail) => {
          const button = h(
            "button",
            {
              class: "btn secondary",
              attrs: { type: "button", "aria-expanded": String(mail.id === openId), style: "justify-content: flex-start; flex: 1; text-align: left" },
            },
            mail.read ? null : h("span", { class: "unread-dot" }),
            mail.read ? null : h("span", { class: "sr", text: "Unread," }),
            h("span", { text: mail.subject, attrs: { style: mail.read ? "font-weight: 400" : "font-weight: 600" } }),
          );
          button.addEventListener("click", () => void open(mail));
          return h("li", {}, button, h("span", { class: "muted sm num", text: shortTime(mail.created_at) }));
        }),
      ),
    );
  };
  const open = async (mail: Mail): Promise<void> => {
    openId = mail.id;
    reader.replaceChildren(h("h2", { text: mail.subject }), body(mail.text));
    if (!mail.read) {
      mail.read = true;
      draw();
      await api("POST", `/inbox/${mail.id}/read`).catch(() => undefined);
    }
    reader.querySelector("h2")?.setAttribute("tabindex", "-1");
    reader.querySelector<HTMLElement>("h2")?.focus();
  };
  let loaded = false;
  const load = async (): Promise<void> => {
    try {
      mails = await api<Mail[]>("GET", "/inbox");
      loaded = true;
      draw();
    } catch (error) {
      if (!loaded) list.replaceChildren(loadFailed("your inbox", () => void poller.now()));
      throw error;
    }
  };
  const poller = poll(load, stale);
  void poller.now();
  return poller.stop;
}
