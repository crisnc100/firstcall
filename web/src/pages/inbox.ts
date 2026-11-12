// The in-app copies of alert emails. Only the plain-text body is shown: the HTML part
// carries buyer text and the app has no sanitiser for it.

import { api } from "../api";
import { h, link } from "../dom";
import { shortTime } from "../format";
import { emptyState, loadFailed, poll, skeleton, staleLine } from "../poller";
import type { Cleanup } from "../router";
import { setTitle, setUnread } from "../shell";

interface Mail {
  id: string;
  subject: string;
  text: string;
  links: { label: string; path: string }[];
  created_at: string;
  read: boolean;
}

/** The digest's first line repeats its subject, which the reader already shows as the heading. */
function withoutHeading(text: string, subject: string): string {
  const [first, ...rest] = text.split("\n");
  return first && subject.startsWith(first.trim()) ? rest.join("\n").replace(/^\n+/, "") : text;
}

/** A path inside this app: one leading "/", never "//", which a browser reads as another host. */
function inApp(path: string): boolean {
  return path.startsWith("/") && !path.startsWith("//") && !path.includes("\\");
}

/** The body as plain text, then the server's own links to the leads it names. A link is
 * never made from the text: buyer names and items are in it. */
function body(mail: Mail): HTMLElement {
  const links = mail.links.filter((l) => inApp(l.path));
  return h(
    "div",
    {},
    h("div", { class: "msg", text: withoutHeading(mail.text, mail.subject) }),
    links.length
      ? h(
          "ul",
          { class: "list-plain", attrs: { "aria-label": "Leads in this alert" } },
          ...links.map((l) => h("li", {}, link(l.path, `Open ${l.label || "the lead"}`))),
        )
      : null,
  );
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
    reader.replaceChildren(h("h2", { text: mail.subject }), body(mail));
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
