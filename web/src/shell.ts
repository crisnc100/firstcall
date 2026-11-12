// The signed-in frame: top bar, bottom tab bar on phones, and the main area a page fills.
// Nav items come from the session; an item the user cannot use is not shown at all.

import { api, session, signOut, type Session } from "./api";
import { h, link } from "./dom";
import { icon, type IconName } from "./icons";
import { navigate } from "./router";

interface NavItem {
  href: string;
  label: string;
  short: string;
  icon: IconName;
}

const ITEMS: Record<string, NavItem> = {
  pool: { href: "/pool", label: "Pool", short: "Pool", icon: "list" },
  mine: { href: "/mine", label: "My leads", short: "My leads", icon: "user" },
  unassigned: { href: "/unassigned", label: "Unassigned", short: "Unassigned", icon: "pin" },
  waiting: { href: "/waiting", label: "Waiting longest", short: "Waiting", icon: "hourglass" },
  tray: { href: "/tray", label: "Not-a-lead tray", short: "Tray", icon: "tray" },
  inbox: { href: "/inbox", label: "Inbox", short: "Inbox", icon: "inbox" },
  users: { href: "/admin/users", label: "Users", short: "Users", icon: "users" },
  alerts: { href: "/alerts", label: "Alerts on this device", short: "Alerts", icon: "bell" },
  account: { href: "/account", label: "Account", short: "Account", icon: "user" },
  more: { href: "/account", label: "More", short: "More", icon: "more" },
};

export function inTeam(s: Session): boolean {
  return Object.keys(s.user.teams).length > 0;
}

export function watches(s: Session): boolean {
  return s.user.roles.includes("monitor") || s.user.roles.includes("admin");
}

function topItems(s: Session): NavItem[] {
  const items: NavItem[] = [];
  if (inTeam(s)) items.push(ITEMS.pool, ITEMS.mine);
  if (watches(s)) items.push(ITEMS.unassigned, ITEMS.waiting, ITEMS.tray);
  if (s.user.roles.includes("admin")) items.push(ITEMS.users);
  return items;
}

function bottomItems(s: Session): NavItem[] {
  if (inTeam(s) && watches(s)) return [ITEMS.pool, ITEMS.unassigned, ITEMS.mine, ITEMS.more];
  if (watches(s)) return [ITEMS.unassigned, ITEMS.waiting, ITEMS.tray, ITEMS.account];
  return [ITEMS.pool, ITEMS.mine, ITEMS.inbox, ITEMS.account];
}

/** Everything the bottom bar has no room for, for the Account page. */
export function overflowItems(s: Session): NavItem[] {
  const shown = new Set(bottomItems(s).map((item) => item.href));
  return [...topItems(s), ITEMS.inbox, ITEMS.alerts].filter((item) => !shown.has(item.href));
}

function current(href: string): boolean {
  const path = window.location.pathname;
  return path === href || path.startsWith(`${href}/`) || (href === "/pool" && path === "/");
}

function navLink(item: NavItem, withIcon: boolean): HTMLAnchorElement {
  const anchor = h("a", {
    attrs: { href: item.href, "data-link": true, "aria-current": current(item.href) ? "page" : undefined },
  });
  if (withIcon) anchor.append(icon(item.icon, true), item.short);
  else anchor.append(item.label);
  return anchor;
}

let unread = 0;
let unreadShown: { badge: HTMLElement; words: HTMLElement } | null = null;

/** Show the unread count in the top bar; the Inbox page calls it as mail is read. */
export function setUnread(count: number): void {
  unread = count;
  if (!unreadShown) return;
  unreadShown.badge.textContent = String(count);
  unreadShown.badge.hidden = count === 0;
  unreadShown.words.textContent = count === 1 ? "1 unread" : `${count} unread`;
}

/** Re-read the unread count; every list poll calls it, so the badge keeps up. */
export async function refreshUnread(): Promise<void> {
  try {
    const rows = await api<{ read: boolean }[]>("GET", "/inbox");
    setUnread(rows.filter((row) => !row.read).length);
  } catch {
    return;
  }
}

export function setTitle(title: string): void {
  document.title = `${title} · Firstcall`;
  const slot = document.querySelector(".page-title");
  if (slot) slot.textContent = title;
}

/** Draw the frame and return the main element. */
export function frame(root: HTMLElement): HTMLElement {
  const s = session();
  if (!s) throw new Error("frame needs a session");
  const badge = h("span", { class: "count num", text: String(unread), attrs: { hidden: unread === 0 } });
  const unreadWords = h("span", { class: "sr" });
  const inbox = h(
    "a",
    { class: "inbox", attrs: { href: "/inbox", "data-link": true } },
    icon("inbox", true),
    h("span", { class: "sr", text: "Inbox," }),
    badge,
    unreadWords,
  );
  const menu = h("div", { class: "menu", attrs: { hidden: true, id: "account-menu" } });
  const who = h("button", {
    class: "btn secondary who",
    text: s.user.name,
    attrs: { "aria-expanded": "false", "aria-controls": "account-menu", type: "button" },
  });
  const closeMenu = (): void => {
    menu.hidden = true;
    who.setAttribute("aria-expanded", "false");
  };
  who.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
    who.setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) menu.querySelector<HTMLElement>("a,button")?.focus();
  });
  menu.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeMenu();
      who.focus();
    }
  });
  menu.append(
    link("/alerts", "Alerts on this device"),
    h("button", { text: "Sign out", attrs: { type: "button" }, on: { click: () => void leave() } }),
  );
  menu.addEventListener("click", (event) => {
    if ((event.target as Element).closest("a")) closeMenu();
  });

  const header = h(
    "header",
    { class: "topbar" },
    h(
      "div",
      { class: "in" },
      h("a", { class: "mark", text: "Firstcall", attrs: { href: "/", "data-link": true } }),
      h("span", { class: "page-title" }),
      h("nav", { class: "tabs", attrs: { "aria-label": "Main" } }, ...topItems(s).map((i) => navLink(i, false))),
      h("div", { class: "right" }, inbox, who, menu),
    ),
  );
  const main = h("main", { attrs: { id: "main", tabindex: "-1" } });
  const bottom = h(
    "nav",
    { class: "bottombar", attrs: { "aria-label": "Main, short" } },
    ...bottomItems(s).map((i) => navLink(i, true)),
  );
  root.replaceChildren(
    h("a", { class: "skip", text: "Skip to content", attrs: { href: "#main" } }),
    header,
    main,
    bottom,
  );
  unreadShown = { badge, words: unreadWords };
  setUnread(unread);
  void refreshUnread();
  return main;
}

const RETRY_SIGN_OUT_MS = 5000;

/** Sign out, and show the sign-in page only once the server confirms. Until then the app
 * is covered and the sign-out is retried: on a shared browser, a sign-in page shown over a
 * live session would hand the account to the next person. */
export async function leave(): Promise<void> {
  let waiting: HTMLElement | null = null;
  let retry = 0;
  const attempt = async (): Promise<void> => {
    window.clearTimeout(retry);
    try {
      await signOut();
    } catch {
      waiting ??= blockUntilSignedOut(() => void attempt());
      retry = window.setTimeout(() => void attempt(), RETRY_SIGN_OUT_MS);
      return;
    }
    waiting?.remove();
    navigate("/signin", true);
  };
  await attempt();
}

function blockUntilSignedOut(tryNow: () => void): HTMLElement {
  const again = h("button", { class: "btn primary", text: "Try again now", attrs: { type: "button" } });
  again.addEventListener("click", tryNow);
  const cover = h(
    "div",
    { class: "signing-out", attrs: { role: "alertdialog", "aria-modal": "true", "aria-labelledby": "signing-out-title" } },
    h(
      "div",
      { class: "panel" },
      h("h1", { text: "Not signed out yet", attrs: { id: "signing-out-title" } }),
      h("p", {
        text: "Firstcall couldn't be reached, so this browser is still signed in. Trying again every few seconds. Stay here until it says you're signed out.",
      }),
      again,
    ),
  );
  document.body.append(cover);
  again.focus();
  return cover;
}

export function homeFor(s: Session): string {
  return inTeam(s) ? "/pool" : watches(s) ? "/unassigned" : "/inbox";
}
