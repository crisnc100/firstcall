// Push alerts for this browser. Permission is asked only from the button, never on load.

import { api } from "../api";
import { h, link, remember } from "../dom";
import { icon } from "../icons";
import { busy } from "../leadlist";
import type { Cleanup } from "../router";
import { setTitle } from "../shell";

type PushState = "unconfigured" | "unsupported" | "ios" | "blocked" | "off" | "on";

const TEXT: Record<PushState, string> = {
  unconfigured:
    "Push alerts aren't set up on this server. You'll still get alerts by email and in the Inbox.",
  unsupported: "This browser can't show push alerts.",
  ios: "On iPhone, add Firstcall to your Home Screen first: tap Share, then Add to Home Screen. Then open it from there.",
  blocked: "Alerts are blocked for this site. Allow notifications in your browser's site settings, then come back.",
  off: "Push alerts are off on this device.",
  on: "Push alerts are on. You'll get one alert per new lead in your teams.",
};

function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = (base64url + "=".repeat((4 - (base64url.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

function isIphoneBrowser(): boolean {
  const standalone =
    window.matchMedia("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true;
  return /iPhone|iPad|iPod/.test(navigator.userAgent) && !standalone;
}

async function save(subscription: PushSubscription): Promise<void> {
  const json = subscription.toJSON();
  await api("POST", "/push/subscriptions", {
    endpoint: json.endpoint,
    keys: { p256dh: json.keys?.p256dh ?? "", auth: json.keys?.auth ?? "" },
  });
}

interface Found {
  state: PushState;
  key: string | null;
  registration: ServiceWorkerRegistration | null;
  subscription: PushSubscription | null;
}

async function find(): Promise<Found> {
  const none = { key: null, registration: null, subscription: null };
  const { public_key: key } = await api<{ public_key: string | null }>("GET", "/push/key");
  if (!key) return { state: "unconfigured", ...none };
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    return { state: isIphoneBrowser() ? "ios" : "unsupported", ...none };
  }
  if (Notification.permission === "denied") return { state: "blocked", ...none };
  const registration = await navigator.serviceWorker.register("/sw.js");
  const subscription = await registration.pushManager.getSubscription();
  if (subscription) await save(subscription);
  return { state: subscription ? "on" : "off", key, registration, subscription };
}

export function alertsPage(main: HTMLElement): Cleanup {
  setTitle("Alerts on this device");
  const status = h("p", { class: "line", attrs: { role: "status" } }, "Checking this browser…");
  const control = h("div", {});
  const error = h("p", { class: "danger-text", attrs: { hidden: true } });
  main.replaceChildren(
    h("h1", { text: "Alerts on this device" }),
    status,
    control,
    error,
    h("p", {
      class: "muted sm",
      text: "Email alerts are always on. They group alerts that come within a minute into one email.",
    }),
  );
  const draw = (found: Found): void => {
    status.replaceChildren(icon(found.state === "on" ? "check" : "bell", true), TEXT[found.state]);
    control.replaceChildren();
    if (found.state === "off") {
      const button = h("button", { class: "btn primary", text: "Turn on alerts", attrs: { type: "button" } });
      button.addEventListener("click", () => void turnOn(found, button));
      control.append(button);
    }
    if (found.state === "on") {
      const button = h("button", { class: "btn secondary", text: "Turn off", attrs: { type: "button" } });
      button.addEventListener("click", () => void turnOff(found, button));
      control.append(button);
    }
  };
  const turnOn = async (found: Found, button: HTMLButtonElement): Promise<void> => {
    error.hidden = true;
    busy(button, "Turning on…");
    status.replaceChildren(icon("bell", true), "Asking your browser…");
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted" || !found.registration || !found.key) {
        draw({ ...found, state: permission === "denied" ? "blocked" : "off" });
        return;
      }
      const subscription = await found.registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: keyBytes(found.key),
      });
      await save(subscription);
      draw({ ...found, state: "on", subscription });
    } catch {
      draw({ ...found, state: "off" });
      error.hidden = false;
      error.textContent = "Couldn't turn on alerts. Check your connection and try again.";
    }
  };
  const turnOff = async (found: Found, button: HTMLButtonElement): Promise<void> => {
    busy(button, "Turning off…");
    const subscription = found.subscription;
    try {
      if (subscription) {
        await api("DELETE", "/push/subscriptions", { endpoint: subscription.endpoint });
        await subscription.unsubscribe();
      }
      draw({ ...found, state: "off", subscription: null });
    } catch {
      draw(found);
      error.hidden = false;
      error.textContent = "Couldn't turn off alerts. Check your connection and try again.";
    }
  };
  void find()
    .then(draw)
    .catch(() => {
      status.replaceChildren(icon("alert", true), "Couldn't check alerts on this device.");
    });
  return () => undefined;
}

/** A one-time line at the top of the pool for someone with no push on this device. */
export function alertsNudge(): HTMLElement {
  const box = h("div", { attrs: { hidden: true } });
  if (remember("firstcall.nudge") === "dismissed") return box;
  void find()
    .then((found) => {
      if (found.state !== "off") return;
      const dismiss = h("button", { class: "btn secondary", text: "Not now", attrs: { type: "button" } });
      dismiss.addEventListener("click", () => {
        remember("firstcall.nudge", "dismissed");
        box.hidden = true;
      });
      box.className = "nudge";
      box.replaceChildren(
        icon("bell", true),
        h("span", { text: "Get an alert the moment a lead arrives." }),
        link("/alerts", "Turn on alerts", "btn secondary"),
        dismiss,
      );
      box.hidden = false;
    })
    .catch(() => undefined);
  return box;
}
