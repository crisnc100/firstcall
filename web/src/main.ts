import "./styles/tokens.css";
import "./styles/app.css";

import { loadSession, session, whenSignedOut } from "./api";
import { accountPage } from "./pages/account";
import { alertsPage } from "./pages/alerts";
import { detailPage } from "./pages/detail";
import { inboxPage } from "./pages/inbox";
import { minePage, poolPage, unassignedPage } from "./pages/lists";
import { rememberRoute, signInPage } from "./pages/signin";
import { trayMessagePage, trayPage } from "./pages/tray";
import { usersPage } from "./pages/users";
import { waitingPage } from "./pages/waiting";
import { navigate, route, start, type Cleanup, type Page } from "./router";
import { frame, homeFor, inTeam, watches } from "./shell";

const root = document.getElementById("app") as HTMLElement;
let cleanup: Cleanup = () => undefined;

route("/signin", (_, __) => signInPage(root));
route("/pool", poolPage);
route("/pool/:teamId", poolPage);
route("/mine", minePage);
route("/leads/:id", detailPage);
route("/unassigned", unassignedPage);
route("/waiting", waitingPage);
route("/tray", trayPage);
route("/tray/:id", trayMessagePage);
route("/inbox", inboxPage);
route("/admin/users", usersPage);
route("/alerts", alertsPage);
route("/account", accountPage);

// Pages a role cannot use go to that user's home page rather than showing empty frames.
const ALLOWED: Record<string, (roles: string[], team: boolean) => boolean> = {
  "/pool": (_, team) => team,
  "/mine": (_, team) => team,
  "/unassigned": (roles) => roles.includes("monitor") || roles.includes("admin"),
  "/waiting": (roles) => roles.includes("monitor") || roles.includes("admin"),
  "/tray": (roles) => roles.includes("monitor") || roles.includes("admin"),
  "/admin": (roles) => roles.includes("admin"),
};

whenSignedOut(() => {
  if (window.location.pathname === "/signin") return;
  rememberRoute();
  navigate("/signin?expired=1", true);
});

async function render(page: Page, params: Record<string, string>): Promise<void> {
  cleanup();
  cleanup = () => undefined;
  const path = window.location.pathname;
  if (path === "/signin") {
    cleanup = page(root, params) ?? cleanup;
    return;
  }
  const s = session() ?? (await loadSession().catch(() => null));
  if (!s) {
    rememberRoute();
    navigate("/signin", true);
    return;
  }
  const area = Object.keys(ALLOWED).find((prefix) => path === prefix || path.startsWith(`${prefix}/`));
  if (area && !ALLOWED[area](s.user.roles, inTeam(s))) {
    navigate(homeFor(s), true);
    return;
  }
  const main = frame(root);
  cleanup = page(main, params) ?? cleanup;
  if (watches(s) || inTeam(s)) window.scrollTo(0, 0);
}

start(
  (page, params) => void render(page, params),
  () => {
    const s = session();
    if (s) navigate(homeFor(s), true);
    else void loadSession().then((found) => navigate(found ? homeFor(found) : "/signin", true));
  },
);
