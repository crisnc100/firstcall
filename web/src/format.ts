// Times and waits as the pages show them.

import { serverNow } from "./api";

export function minutesSince(iso: string): number {
  return Math.max(0, (serverNow() - Date.parse(iso)) / 60000);
}

/** "Just now", "4 min", "1 h 05 min", "2 d 3 h". */
export function waitText(minutes: number): string {
  const whole = Math.floor(minutes);
  if (whole < 1) return "Just now";
  if (whole < 60) return `${whole} min`;
  const hours = Math.floor(whole / 60);
  if (hours < 24) return `${hours} h ${String(whole % 60).padStart(2, "0")} min`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

/** The same wait spelled out for a screen reader. */
export function waitWords(minutes: number): string {
  const whole = Math.floor(minutes);
  if (whole < 1) return "Arrived just now";
  if (whole < 60) return `Waited ${whole} minute${whole === 1 ? "" : "s"}`;
  const hours = Math.floor(whole / 60);
  const rest = whole % 60;
  if (hours < 24) return `Waited ${hours} hour${hours === 1 ? "" : "s"} ${rest} minutes`;
  return `Waited ${Math.floor(hours / 24)} days ${hours % 24} hours`;
}

export function agoText(iso: string): string {
  const minutes = minutesSince(iso);
  return minutes < 1 ? "just now" : `${waitText(minutes)} ago`;
}

/** "10:42" today, "Tue 10:42" this week, "12 Nov" before that. */
export function shortTime(iso: string): string {
  const at = new Date(iso);
  const now = new Date(serverNow());
  const time = at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  if (at.toDateString() === now.toDateString()) return time;
  if (now.getTime() - at.getTime() < 6 * 86400000) {
    return `${at.toLocaleDateString(undefined, { weekday: "short" })} ${time}`;
  }
  return at.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function fullTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export type Urgency = "new" | "waiting" | "late" | "verylate";

/** The urgency step, from the business minutes and team thresholds the API sends when it
 * can, so a lead shows Late exactly when its team escalates. */
export function urgency(
  waitBusiness: number | null,
  wallMinutes: number,
  thresholds: { late: number; very_late: number } | null,
): Urgency {
  const waited = waitBusiness ?? wallMinutes;
  const late = thresholds?.late ?? 15;
  const veryLate = thresholds?.very_late ?? 60;
  if (waited >= veryLate) return "verylate";
  if (waited >= late) return "late";
  if (waited >= 5) return "waiting";
  return "new";
}

export const URGENCY_WORD: Record<Urgency, string> = {
  new: "New",
  waiting: "Waiting",
  late: "Late",
  verylate: "Very late",
};
