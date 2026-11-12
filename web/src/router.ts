// History routing. The server answers every page address with index.html, so a reload on
// any route works.

export type Cleanup = () => void;
export type Page = (main: HTMLElement, params: Record<string, string>) => Cleanup | void;

interface Route {
  pattern: RegExp;
  keys: string[];
  page: Page;
}

const routes: Route[] = [];
let render: (page: Page, params: Record<string, string>) => void = () => undefined;
let fallback: () => void = () => undefined;

export function route(path: string, page: Page): void {
  const keys: string[] = [];
  const pattern = new RegExp(
    `^${path.replace(/:([a-zA-Z]+)/g, (_, key: string) => {
      keys.push(key);
      return "([^/]+)";
    })}/?$`,
  );
  routes.push({ pattern, keys, page });
}

export function start(
  renderer: (page: Page, params: Record<string, string>) => void,
  noMatch: () => void,
): void {
  render = renderer;
  fallback = noMatch;
  window.addEventListener("popstate", resolve);
  document.addEventListener("click", (event) => {
    const target = (event.target as Element | null)?.closest("a[data-link]");
    if (!(target instanceof HTMLAnchorElement)) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    if (target.origin !== window.location.origin) return;
    event.preventDefault();
    navigate(target.pathname + target.search);
  });
  resolve();
}

export function navigate(path: string, replace = false): void {
  if (replace) window.history.replaceState(null, "", path);
  else window.history.pushState(null, "", path);
  resolve();
}

export function resolve(): void {
  const path = window.location.pathname;
  for (const { pattern, keys, page } of routes) {
    const match = pattern.exec(path);
    if (!match) continue;
    const params: Record<string, string> = {};
    keys.forEach((key, index) => {
      params[key] = decodeURIComponent(match[index + 1] ?? "");
    });
    render(page, params);
    return;
  }
  fallback();
}
