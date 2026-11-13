// Every piece of API text reaches the page as a text node. There is no innerHTML anywhere
// in the app: buyer names and message text are untrusted.

export type Child = Node | string | number | null | undefined | false;

interface Props {
  class?: string;
  text?: string;
  attrs?: Record<string, string | boolean | undefined>;
  on?: Partial<{ [K in keyof HTMLElementEventMap]: (event: HTMLElementEventMap[K]) => void }>;
}

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Props = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const element = document.createElement(tag);
  if (props.class) element.className = props.class;
  if (props.text !== undefined) element.textContent = props.text;
  for (const [name, value] of Object.entries(props.attrs ?? {})) {
    if (value === true) element.setAttribute(name, "");
    else if (value !== false && value !== undefined) element.setAttribute(name, value);
  }
  for (const [name, handler] of Object.entries(props.on ?? {})) {
    element.addEventListener(name, handler as EventListener);
  }
  append(element, ...children);
  return element;
}

export function append(parent: Node, ...children: Child[]): void {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    parent.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(element: Element): void {
  element.replaceChildren();
}

export function link(href: string, text: string, className = ""): HTMLAnchorElement {
  return h("a", { class: className, attrs: { href, "data-link": true }, text });
}

let liveRegion: HTMLElement | null = null;

/** The page's one polite live region, for results such as "Already claimed by a teammate". */
export function announce(message: string): void {
  liveRegion ??= document.getElementById("live");
  if (!liveRegion) return;
  liveRegion.textContent = "";
  window.setTimeout(() => {
    if (liveRegion) liveRegion.textContent = message;
  }, 50);
}

export function remember(key: string, value?: string): string | null {
  try {
    if (value !== undefined) window.localStorage.setItem(key, value);
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
