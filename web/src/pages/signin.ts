import { ApiError, signIn } from "../api";
import { h } from "../dom";
import { icon } from "../icons";
import { busy, unbusy } from "../leadlist";
import { navigate, type Cleanup } from "../router";
import { homeFor } from "../shell";

const RETURN_KEY = "firstcall.return";

export function rememberRoute(): void {
  try {
    window.sessionStorage.setItem(RETURN_KEY, window.location.pathname + window.location.search);
  } catch {
    // A private window may refuse storage; the user lands on their home page instead.
  }
}

function takeRoute(): string | null {
  try {
    const path = window.sessionStorage.getItem(RETURN_KEY);
    window.sessionStorage.removeItem(RETURN_KEY);
    return path && path.startsWith("/") && !path.startsWith("//") && path !== "/signin" ? path : null;
  } catch {
    return null;
  }
}

export function signInPage(root: HTMLElement): Cleanup {
  document.title = "Sign in · Firstcall";
  const expired = new URLSearchParams(window.location.search).get("expired") === "1";
  const email = h("input", { attrs: { id: "email", type: "email", autocomplete: "username", required: true } });
  const password = h("input", {
    attrs: { id: "password", type: "password", autocomplete: "current-password", required: true },
  });
  const show = h("button", { class: "btn secondary reveal", text: "Show", attrs: { type: "button", "aria-pressed": "false" } });
  show.addEventListener("click", () => {
    const hidden = password.type === "password";
    password.type = hidden ? "text" : "password";
    show.textContent = hidden ? "Hide" : "Show";
    show.setAttribute("aria-pressed", String(hidden));
  });
  const error = h("p", { class: "danger-text", attrs: { id: "signin-error", hidden: true, role: "alert" } });
  const submit = h("button", { class: "btn primary", text: "Sign in", attrs: { type: "submit" } });
  const form = h(
    "form",
    { attrs: { novalidate: true } },
    h("div", { class: "field" }, h("label", { text: "Email", attrs: { for: "email" } }), email),
    h("div", { class: "field" }, h("label", { text: "Password", attrs: { for: "password" } }), h("div", { class: "pw" }, password, show)),
    error,
    submit,
  );
  const fail = (text: string): void => {
    error.hidden = false;
    error.replaceChildren(icon("alert"), text);
    email.setAttribute("aria-describedby", "signin-error");
    password.setAttribute("aria-describedby", "signin-error");
    unbusy(submit, "Sign in");
    email.focus();
  };
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    error.hidden = true;
    busy(submit, "Signing in…");
    signIn(email.value, password.value)
      .then((s) => navigate(takeRoute() ?? homeFor(s), true))
      .catch((caught: unknown) => {
        if (caught instanceof ApiError && caught.status === 401) fail("That email and password don't match.");
        else if (caught instanceof ApiError && caught.status === 429) fail("Too many tries. Wait a minute, then try again.");
        else fail("Can't reach Firstcall. Check your connection.");
      });
  });
  root.replaceChildren(
    h(
      "main",
      { class: "signin", attrs: { id: "main" } },
      h("p", { class: "mark", text: "Firstcall" }),
      h("h1", { text: "Sign in", attrs: { style: "margin-bottom: 24px" } }),
      expired ? h("p", { class: "notice", text: "You were signed out. Sign in to carry on." }) : null,
      form,
      h("p", { class: "muted sm", text: "Forgot your password? Ask your admin to set a new one." }),
    ),
  );
  email.focus();
  return () => undefined;
}
