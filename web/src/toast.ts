import { h } from "./dom";
import { icon } from "./icons";

let timer = 0;

/** One toast at a time, in a polite live region. A toast never carries an error that needs
 * action; those stay inline where they happened. */
export function toast(message: string, linkText?: string, href?: string): void {
  const region = document.getElementById("toast");
  if (!region) return;
  window.clearTimeout(timer);
  const parts: Node[] = [icon("check", true, "ok"), h("span", { text: message })];
  if (linkText && href) parts.push(h("a", { attrs: { href, "data-link": true }, text: linkText }));
  region.replaceChildren(h("div", { class: "toast" }, ...parts));
  const hide = (): void => {
    if (region.matches(":hover") || region.contains(document.activeElement)) {
      timer = window.setTimeout(hide, 2000);
      return;
    }
    region.replaceChildren();
  };
  timer = window.setTimeout(hide, 6000);
}
