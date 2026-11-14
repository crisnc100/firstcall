// Working a lead: notes and the contacts they log, the buyer profile, the open follow-up,
// and the outcome, reopen and delete controls. Plain on purpose; the redesign comes later.

import { api, ApiError, type LeadDetail, type Profile } from "../api";
import { announce, h } from "../dom";
import { shortTime } from "../format";
import { busy, unbusy } from "../leadlist";
import { toast } from "../toast";

export const ACTION_WORD: Record<string, string> = {
  call: "Called",
  text: "Texted",
  email: "Emailed",
  meeting: "Met",
};
export const LOST_REASONS: [string, string][] = [
  ["price", "Price"],
  ["location", "Location"],
  ["timing", "Timing"],
  ["bought_elsewhere", "Bought elsewhere"],
  ["not_ready", "Not ready to buy"],
  ["unreachable", "Couldn't reach them"],
  ["other", "Other"],
];
const AUTHORITY: [string, string][] = [
  ["unknown", "Not known yet"],
  ["decision_maker", "Decides alone"],
  ["shares_decision", "Shares the decision"],
  ["not_decision_maker", "Someone else decides"],
];

export function reasonWord(reason: string | null): string {
  return LOST_REASONS.find(([code]) => code === reason)?.[1] ?? "";
}

function failure(error: unknown, what: string): string {
  if (error instanceof ApiError && error.status === 422) {
    return error.detail.charAt(0).toUpperCase() + error.detail.slice(1);
  }
  if (error instanceof ApiError && error.status === 409) return "This lead has moved on.";
  return `Couldn't ${what}. Check your connection and try again.`;
}

async function attempt(
  button: HTMLButtonElement,
  working: string,
  label: string,
  status: HTMLElement,
  call: () => Promise<unknown>,
  done: string,
  what: string,
  reload: () => Promise<void>,
): Promise<void> {
  status.replaceChildren();
  busy(button, working);
  try {
    await call();
  } catch (error) {
    unbusy(button, label);
    const text = failure(error, what);
    status.replaceChildren(h("p", { class: "danger-text", text }));
    announce(text);
    return;
  }
  toast(done);
  announce(done);
  await reload();
}

/** Time to first contact, the open follow-up, notes and the profile: everything a page
 * shows about working the lead. Empty for a viewer the server gave none of it to. */
export function workSections(lead: LeadDetail, reload: () => Promise<void>): HTMLElement[] {
  const parts: HTMLElement[] = [];
  if (lead.first_contact_minutes !== null && lead.first_contact_business_minutes !== null) {
    parts.push(
      h("p", {
        class: "num first-contact",
        text: `First contact after ${Math.round(lead.first_contact_business_minutes)} business minutes (${Math.round(lead.first_contact_minutes)} min on the clock)`,
      }),
    );
  }
  if (lead.followup) {
    const due = lead.followup.overdue ? `Overdue since ${lead.followup.due_on}` : `Due ${lead.followup.due_on}`;
    parts.push(
      h(
        "div",
        { class: "notice followup" },
        h("p", { attrs: { style: "margin: 0" } }, h("strong", { text: "Follow-up: " }), due),
        h("p", { class: "muted", text: lead.followup.next_step, attrs: { style: "margin: 4px 0 0" } }),
      ),
    );
  }
  if (lead.notes !== undefined) parts.push(...notesSection(lead, reload));
  if (lead.profile !== undefined) parts.push(...profileSection(lead, reload));
  return parts;
}

function notesSection(lead: LeadDetail, reload: () => Promise<void>): HTMLElement[] {
  const notes = lead.notes ?? [];
  const logged = new Map<string, string[]>();
  for (const contact of lead.contacts ?? []) {
    logged.set(contact.note_id, [...(logged.get(contact.note_id) ?? []), ACTION_WORD[contact.kind] ?? contact.kind]);
  }
  const list = h(
    "ul",
    { class: "notes" },
    ...notes.map((note) =>
      h(
        "li",
        {},
        h(
          "div",
          { class: "line muted sm" },
          `${note.author ?? "Someone"} · ${shortTime(note.created_at)}`,
          ...(logged.get(note.id) ?? []).map((word) => h("span", { class: "badge", text: `${word} · contact logged` })),
        ),
        h("div", { class: "msg", text: note.text }),
      ),
    ),
  );
  const parts: HTMLElement[] = [h("h2", { text: `Notes (${notes.length})` })];
  if (!notes.length) parts.push(h("p", { class: "muted", text: "No notes yet." }));
  else parts.push(list);
  if (lead.work.includes("note")) parts.push(noteForm(lead, reload));
  return parts;
}

function noteForm(lead: LeadDetail, reload: () => Promise<void>): HTMLElement {
  const text = h("textarea", { attrs: { id: "note-text", rows: "3", maxlength: "5000" } });
  const fields: HTMLElement[] = [h("label", { text: "Add a note", attrs: { for: "note-text" } }), text];
  const action = h("select", { attrs: { id: "note-action" } }, h("option", { text: "No contact", attrs: { value: "" } }));
  if (lead.work.includes("log_contact")) {
    for (const [kind, word] of Object.entries(ACTION_WORD)) action.append(h("option", { text: word, attrs: { value: kind } }));
    fields.push(h("label", { text: "This note records", attrs: { for: "note-action" } }), action);
  }
  const status = h("div", { attrs: { role: "status" } });
  const save = h("button", { class: "btn primary", text: "Save note", attrs: { type: "submit" } });
  const form = h("form", { class: "note-form" }, ...fields, h("div", { class: "row-buttons" }, save), status);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const body = { text: text.value, action: action.value || null };
    const done = body.action ? "Note saved and contact logged." : "Note saved.";
    void attempt(save, "Saving…", "Save note", status, () => api("POST", `/leads/${lead.id}/notes`, body), done, "save the note", reload);
  });
  return form;
}

function budgetText(profile: Profile): string {
  const money = (value: number): string => `$${value.toLocaleString("en-US")}`;
  const range =
    profile.budget_min !== null && profile.budget_max !== null
      ? profile.budget_min === profile.budget_max
        ? money(profile.budget_min)
        : `${money(profile.budget_min)} to ${money(profile.budget_max)}`
      : profile.budget_min !== null
        ? `From ${money(profile.budget_min)}`
        : profile.budget_max !== null
          ? `Up to ${money(profile.budget_max)}`
          : "";
  return [range, profile.budget_text ?? ""].filter(Boolean).join(". ");
}

function profileSection(lead: LeadDetail, reload: () => Promise<void>): HTMLElement[] {
  const profile = lead.profile;
  const value = (text: string | null | undefined): HTMLElement =>
    text ? h("dd", { text }) : h("dd", { class: "muted", text: "Not known yet" });
  const authority = profile
    ? [AUTHORITY.find(([code]) => code === profile.authority)?.[1], profile.authority_others].filter(Boolean).join(". ")
    : "";
  const timeline = profile ? [profile.timeline_month, profile.timeline_text].filter(Boolean).join(". ") : "";
  const facts = h(
    "dl",
    { class: "profile" },
    h("dt", { text: "Budget" }),
    value(profile ? budgetText(profile) : null),
    h("dt", { text: "Who decides" }),
    value(profile && profile.authority !== "unknown" ? authority : null),
    h("dt", { text: "Needs" }),
    value(profile?.need),
    h("dt", { text: "Timeline" }),
    value(timeline),
    h("dt", { text: "Notes" }),
    value(profile?.notes),
  );
  const parts: HTMLElement[] = [h("h2", { text: "Buyer profile" }), facts];
  if (lead.work.includes("note")) parts.push(profileEditor(lead, reload));
  return parts;
}

function profileEditor(lead: LeadDetail, reload: () => Promise<void>): HTMLElement {
  const profile = lead.profile;
  const trigger = h("button", { class: "btn secondary", text: "Edit profile", attrs: { type: "button", "aria-expanded": "false" } });
  const wrap = h("div", {}, trigger);
  trigger.addEventListener("click", () => {
    const existing = wrap.querySelector("form");
    if (existing) {
      existing.remove();
      trigger.setAttribute("aria-expanded", "false");
      return;
    }
    const input = (id: string, label: string, current: string | number | null | undefined, type = "text"): [HTMLElement, HTMLInputElement] => {
      const field = h("input", { attrs: { id: `profile-${id}`, type, value: current === null || current === undefined ? "" : String(current) } });
      return [h("label", { text: label, attrs: { for: `profile-${id}` } }), field];
    };
    const [budgetLabel, budget] = input("budget-text", "Budget, in words", profile?.budget_text);
    const [minLabel, min] = input("budget-min", "Budget from ($)", profile?.budget_min, "number");
    const [maxLabel, max] = input("budget-max", "Budget up to ($)", profile?.budget_max, "number");
    const who = h("select", { attrs: { id: "profile-authority" } }, ...AUTHORITY.map(([code, word]) => h("option", { text: word, attrs: { value: code } })));
    who.value = profile?.authority ?? "unknown";
    const [othersLabel, others] = input("others", "Who else decides", profile?.authority_others);
    const [needLabel, need] = input("need", "Needs", profile?.need);
    const [timelineLabel, timeline] = input("timeline", "Timeline, in words", profile?.timeline_text);
    const [monthLabel, month] = input("month", "Timeline month", profile?.timeline_month, "month");
    const [notesLabel, notes] = input("notes", "Notes", profile?.notes);
    const status = h("div", { attrs: { role: "status" } });
    const save = h("button", { class: "btn primary", text: "Save profile", attrs: { type: "submit" } });
    const form = h(
      "form",
      { class: "panel profile-form" },
      budgetLabel, budget, minLabel, min, maxLabel, max,
      h("label", { text: "Who decides", attrs: { for: "profile-authority" } }), who,
      othersLabel, others, needLabel, need, timelineLabel, timeline, monthLabel, month, notesLabel, notes,
      h("div", { class: "row-buttons" }, save),
      status,
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const whole = (text: string): number | null => (text.trim() === "" ? null : Number(text));
      const wanted: Record<string, unknown> = {
        budget_text: budget.value,
        budget_min: whole(min.value),
        budget_max: whole(max.value),
        authority: who.value,
        authority_others: others.value,
        need: need.value,
        timeline_text: timeline.value,
        timeline_month: month.value || null,
        notes: notes.value,
      };
      // Only fields the person changed are sent, so the rest stay open to the model.
      const before: Record<string, unknown> = { ...(profile ?? {}) };
      const changes: Record<string, unknown> = {};
      for (const [name, next] of Object.entries(wanted)) {
        const old = before[name] ?? (name === "authority" ? "unknown" : null);
        const empty = next === "" ? null : next;
        if (empty !== old) changes[name] = next;
      }
      void attempt(save, "Saving…", "Save profile", status, () => api("PUT", `/leads/${lead.id}/profile`, changes), "Profile saved.", "save the profile", reload);
    });
    wrap.append(form);
    trigger.setAttribute("aria-expanded", "true");
    budget.focus();
  });
  return wrap;
}

/** Outcome, nurture, resume, reopen and delete, for the action column. */
export function workControls(lead: LeadDetail, reload: () => Promise<void>, gone: () => void): HTMLElement[] {
  const status = h("div", { attrs: { role: "status" } });
  const parts: HTMLElement[] = [];
  const post = (path: string, body?: unknown) => () => api("POST", `/leads/${lead.id}/${path}`, body);
  const simple = (label: string, working: string, path: string, done: string, body?: unknown): HTMLButtonElement => {
    const button = h("button", { class: "btn secondary", text: label, attrs: { type: "button" } });
    button.addEventListener("click", () => void attempt(button, working, label, status, post(path, body), done, label.toLowerCase(), reload));
    return button;
  };
  if (lead.work.includes("set_outcome")) {
    parts.push(simple("Mark won", "Saving…", "outcome", "Marked won.", { outcome: "won" }));
    parts.push(lostPanel(lead, reload, status));
    parts.push(simple("Mark not real", "Saving…", "outcome", "Marked not real.", { outcome: "not_real" }));
  }
  if (lead.work.includes("set_nurture")) parts.push(simple("Move to nurture", "Saving…", "nurture", "Moved to nurture."));
  if (lead.work.includes("resume")) parts.push(simple("Resume", "Resuming…", "resume", "Back to owned."));
  if (lead.work.includes("reopen")) parts.push(simple("Reopen", "Reopening…", "reopen", "Reopened."));
  if (lead.work.includes("delete")) parts.push(deletePanel(lead, status, gone));
  if (parts.length) parts.push(status);
  return parts;
}

function lostPanel(lead: LeadDetail, reload: () => Promise<void>, status: HTMLElement): HTMLElement {
  const trigger = h("button", { class: "btn secondary", text: "Mark lost…", attrs: { type: "button", "aria-expanded": "false" } });
  const wrap = h("div", {}, trigger);
  trigger.addEventListener("click", () => {
    const existing = wrap.querySelector(".panel");
    if (existing) {
      existing.remove();
      trigger.setAttribute("aria-expanded", "false");
      return;
    }
    const reason = h("select", { attrs: { id: "lost-reason" } }, ...LOST_REASONS.map(([code, word]) => h("option", { text: word, attrs: { value: code } })));
    const other = h("input", { attrs: { id: "lost-other", type: "text", maxlength: "1000" } });
    const competitor = h("input", { attrs: { id: "lost-competitor", type: "text", maxlength: "200" } });
    const go = h("button", { class: "btn primary", text: "Mark lost", attrs: { type: "button" } });
    go.addEventListener("click", () => {
      const body = {
        outcome: "lost",
        reason: reason.value,
        reason_text: reason.value === "other" ? other.value : null,
        competitor: competitor.value || null,
      };
      void attempt(go, "Saving…", "Mark lost", status, () => api("POST", `/leads/${lead.id}/outcome`, body), "Marked lost.", "mark it lost", reload);
    });
    const panel = h(
      "div",
      { class: "panel" },
      h("label", { text: "Why it was lost", attrs: { for: "lost-reason" } }),
      reason,
      h("label", { text: "If other, what happened", attrs: { for: "lost-other" } }),
      other,
      h("label", { text: "Who they went with (optional)", attrs: { for: "lost-competitor" } }),
      competitor,
      h("div", { class: "row-buttons" }, go),
    );
    wrap.append(panel);
    trigger.setAttribute("aria-expanded", "true");
    reason.focus();
  });
  return wrap;
}

function deletePanel(lead: LeadDetail, status: HTMLElement, gone: () => void): HTMLElement {
  const trigger = h("button", { class: "btn danger", text: "Delete lead…", attrs: { type: "button", "aria-expanded": "false" } });
  const wrap = h("div", {}, trigger);
  trigger.addEventListener("click", () => {
    const existing = wrap.querySelector(".panel");
    if (existing) {
      existing.remove();
      trigger.setAttribute("aria-expanded", "false");
      return;
    }
    const go = h("button", { class: "btn danger", text: "Delete this lead for good", attrs: { type: "button" } });
    go.addEventListener("click", () => {
      status.replaceChildren();
      busy(go, "Deleting…");
      api("DELETE", `/leads/${lead.id}`)
        .then(() => {
          toast("Lead deleted.");
          announce("Lead deleted.");
          gone();
        })
        .catch((error: unknown) => {
          unbusy(go, "Delete this lead for good");
          status.replaceChildren(h("p", { class: "danger-text", text: failure(error, "delete the lead") }));
        });
    });
    const panel = h(
      "div",
      { class: "panel" },
      h("p", { text: "This removes the buyer's details, messages, notes and profile. It can't be undone." }),
      h("div", { class: "row-buttons" }, go),
    );
    wrap.append(panel);
    trigger.setAttribute("aria-expanded", "true");
    go.focus();
  });
  return wrap;
}
