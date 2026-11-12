import { session } from "../api";
import { h } from "../dom";
import { icon } from "../icons";
import type { Cleanup } from "../router";
import { leave, overflowItems, setTitle } from "../shell";

/** What the phone's bottom bar has no room for, and Sign out. */
export function accountPage(main: HTMLElement): Cleanup {
  const s = session();
  setTitle("Account");
  if (!s) return () => undefined;
  const signOut = h("button", { class: "btn secondary", text: "Sign out", attrs: { type: "button" } });
  signOut.addEventListener("click", () => void leave());
  main.replaceChildren(
    h("h1", { text: "Account" }),
    h("p", { class: "muted", text: `${s.user.name} · ${s.user.email}` }),
    h(
      "ul",
      { class: "list-plain" },
      ...overflowItems(s).map((item) =>
        h("li", {}, h("a", { attrs: { href: item.href, "data-link": true } }, icon(item.icon, true), ` ${item.label}`)),
      ),
    ),
    h("p", {}, signOut),
  );
  return () => undefined;
}
