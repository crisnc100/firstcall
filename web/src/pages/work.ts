// Working a lead: notes and the contacts they log, the buyer profile, the open follow-up,
// and the outcome, reopen and delete controls.

import { api, ApiError, type LeadDetail, type Note, type Profile } from "../api";
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
          note.source === "voice" ? h("span", { class: "badge", text: "Voice" }) : null,
          ...(logged.get(note.id) ?? []).map((word) => h("span", { class: "badge", text: `${word} · contact logged` })),
        ),
        // A memo has no text until it is transcribed, and none if that fails.
        note.text ? h("div", { class: "msg", text: note.text }) : null,
        ...readParts(lead, note, reload),
      ),
    ),
  );
  const parts: HTMLElement[] = [h("h2", { text: `Notes (${notes.length})` })];
  if (!notes.length) parts.push(h("p", { class: "muted", text: "No notes yet." }));
  else parts.push(list);
  if (lead.work.includes("note")) parts.push(noteForm(lead, reload));
  return parts;
}

const READ_LINE: Record<string, string> = {
  transcribing: "Transcribing…",
  pending: "Reading this note…",
  needs_model: "Needs a model key",
  failed: "Couldn't read this note",
};
// A memo that never became text. One that did, and whose read then failed, reads as any
// note's failed read.
const NOT_TRANSCRIBED = "Couldn't transcribe this memo. Record it again.";
const CLOSED_WORD: Record<string, string> = {
  confirmed: "confirmed",
  dismissed: "dismissed",
  already_logged: "already logged",
};

/** What the model made of a note: its read state, the tidied note, and each suggested
 * contact with Confirm and Dismiss while it is open. */
function readParts(lead: LeadDetail, note: Note, reload: () => Promise<void>): HTMLElement[] {
  const parts: HTMLElement[] = [];
  const line = note.read_reason === "not_transcribed" ? NOT_TRANSCRIBED : READ_LINE[note.read_state];
  if (line) parts.push(h("p", { class: "muted sm read-state", text: line }));
  if (note.clean_note) parts.push(h("p", { class: "sm tidied" }, h("strong", { text: "Tidied: " }), note.clean_note));
  for (const suggestion of note.suggestions) {
    const word = ACTION_WORD[suggestion.kind] ?? suggestion.kind;
    const row = h("div", { class: "suggestion sm" }, h("span", { text: `Suggested: ${word} — “${suggestion.quote}”` }));
    if (suggestion.state !== "open") {
      row.append(" ", h("span", { class: "badge", text: CLOSED_WORD[suggestion.state] ?? suggestion.state }));
    } else if (lead.work.includes("note")) {
      const status = h("div", { attrs: { role: "status" } });
      const path = `/leads/${lead.id}/suggestions/${suggestion.id}`;
      const button = (label: string, verb: string, done: string): HTMLButtonElement => {
        const node = h("button", { class: "btn secondary", text: label, attrs: { type: "button", "aria-label": `${label} ${word.toLowerCase()}` } });
        node.addEventListener("click", () => void attempt(node, "Saving…", label, status, () => api("POST", `${path}/${verb}`), done, `${verb} the suggestion`, reload));
        return node;
      };
      row.append(
        h("div", { class: "row-buttons" }, button("Confirm", "confirm", "Contact logged."), button("Dismiss", "dismiss", "Suggestion dismissed.")),
        status,
      );
    }
    parts.push(row);
  }
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
  const form = h("form", { class: "note-form" }, ...fields, h("div", { class: "row-buttons" }, save), memo(lead, action, status, reload), status);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const body = { text: text.value, action: action.value || null };
    const done = body.action ? "Note saved and contact logged." : "Note saved.";
    void attempt(save, "Saving…", "Save note", status, () => api("POST", `/leads/${lead.id}/notes`, body), done, "save the note", reload);
  });
  return form;
}

// The first format this browser records wins: Chrome, Edge and Firefox take WebM, Safari
// MP4. The server checks the file whatever its type says.
const MEMO_TYPES = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"];
// The server refuses a memo that decodes past 3:00, so the recorder stops a little short.
const MEMO_STOP_MS = 179_500;

function minutes(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function memoFailure(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 413) return "That memo is over 5 MB.";
    if (error.status === 415) return "That file isn't a recording we can use. Send WebM, MP4, WAV, MP3 or Ogg audio.";
    if (error.status === 429) return "Three of your memos are still being transcribed. Try again when one is done.";
    if (error.status === 503) return "Transcription is busy. Try again shortly.";
  }
  return failure(error, "send the memo");
}

// The server judges a file by its first bytes and refuses a declared type that does not fit
// them. Browsers name a picked file's type unevenly (empty, or video/mp4 for an audio-only
// MP4), so a known extension sets the type sent.
const PICKED_TYPES: Record<string, string> = {
  webm: "audio/webm",
  weba: "audio/webm",
  mp4: "audio/mp4",
  m4a: "audio/mp4",
  wav: "audio/wav",
  mp3: "audio/mpeg",
  ogg: "audio/ogg",
  oga: "audio/ogg",
  opus: "audio/ogg",
};
const PICKABLE = ["audio/*", ...Object.keys(PICKED_TYPES).map((extension) => `.${extension}`)].join(",");

function pickedMemo(file: File): Blob {
  const type = PICKED_TYPES[file.name.split(".").pop()?.toLowerCase() ?? ""];
  return type ? new Blob([file], { type }) : file;
}

/** Record a memo, or send a recording, as a voice note. It takes the note form's contact
 * pick, which is logged as soon as the memo is accepted. */
function memo(lead: LeadDetail, action: HTMLSelectElement, status: HTMLElement, reload: () => Promise<void>): HTMLElement {
  const record = h("button", { class: "btn secondary", text: "Record a memo", attrs: { type: "button" } });
  const stop = h("button", { class: "btn primary", text: "Stop and send", attrs: { type: "button", hidden: true } });
  const timer = h("span", { class: "num memo-timer", text: "0:00 / 3:00", attrs: { hidden: true } });
  const file = h("input", { attrs: { id: "memo-file", type: "file", accept: PICKABLE } });
  const wrap = h(
    "div",
    { class: "memo" },
    h("div", { class: "row-buttons" }, record, stop, timer),
    h("label", { text: "Send a recording", attrs: { for: "memo-file" } }),
    file,
  );
  const canRecord = typeof MediaRecorder !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia);
  if (!canRecord) {
    record.hidden = true;
    wrap.prepend(h("p", { class: "muted sm", text: "This browser can't record here. You can still send a recording." }));
  }

  // From Record, or a picked file, until the upload answers, the lead page's polling leaves
  // this form alone (detail.ts, editing). Another action can still redraw the page and
  // remove it: a recording is then dropped unsent, and an error goes to a toast.
  const hold = (): void => wrap.setAttribute("data-busy", "");
  const ready = (): void => {
    wrap.removeAttribute("data-busy");
    unbusy(record, "Record a memo");
    record.hidden = false;
    stop.hidden = true;
    timer.hidden = true;
    file.disabled = false;
    file.value = "";
  };
  const show = (text: string): void => {
    if (!wrap.isConnected) {
      toast(text);
      return;
    }
    status.replaceChildren(h("p", { class: "danger-text", text }));
    announce(text);
  };
  const send = async (audio: Blob): Promise<void> => {
    const picked = action.value || null;
    const query = picked ? `?action=${encodeURIComponent(picked)}` : "";
    status.replaceChildren();
    hold();
    record.hidden = false;
    stop.hidden = true;
    timer.hidden = true;
    busy(record, "Sending…");
    file.disabled = true;
    try {
      await api("POST", `/leads/${lead.id}/voice${query}`, audio);
    } catch (error) {
      ready();
      show(memoFailure(error));
      return;
    }
    wrap.removeAttribute("data-busy");
    const done = picked ? "Memo sent and contact logged." : "Memo sent. Transcribing…";
    toast(done);
    announce(done);
    await reload();
  };

  file.addEventListener("change", () => {
    const chosen = file.files?.[0];
    if (chosen) void send(pickedMemo(chosen));
  });

  let finish: (() => void) | null = null;
  const startRecording = async (): Promise<void> => {
    status.replaceChildren();
    hold();
    record.disabled = true;
    file.disabled = true;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
    } catch {
      ready();
      show("The microphone is blocked. Allow it for this site, or send a recording.");
      return;
    }
    const release = (): void => {
      for (const track of stream.getTracks()) track.stop();
    };
    // The page was redrawn while the browser asked for the microphone.
    if (!wrap.isConnected) {
      release();
      return;
    }
    const type = MEMO_TYPES.find((candidate) => MediaRecorder.isTypeSupported(candidate));
    const chunks: Blob[] = [];
    let failed = false;
    let tick = 0;
    let limit = 0;
    const cleanup = (): void => {
      window.clearInterval(tick);
      window.clearTimeout(limit);
      release();
      finish = null;
    };
    let recorder: MediaRecorder | null = null;
    try {
      const current = new MediaRecorder(stream, { mimeType: type, audioBitsPerSecond: 32_000 });
      recorder = current;
      current.addEventListener("dataavailable", (event) => {
        if (event.data.size) chunks.push(event.data);
      });
      current.addEventListener("error", () => {
        failed = true;
      });
      current.addEventListener("stop", () => {
        cleanup();
        // Checked here as well as on the timer, so a redraw just before the 3:00 stop still
        // drops the memo.
        if (!wrap.isConnected) return;
        if (failed || !chunks.length) {
          ready();
          show("The recording failed. Record it again, or send a recording.");
          return;
        }
        void send(new Blob(chunks, { type: current.mimeType || type || "" }));
      });
      current.start();
    } catch {
      cleanup();
      ready();
      show("This browser couldn't record. You can still send a recording.");
      return;
    }
    const end = (): void => {
      if (recorder && recorder.state !== "inactive") recorder.stop();
    };
    finish = end;
    const started = Date.now();
    tick = window.setInterval(() => {
      // Another action redrew the page mid-memo: drop it and let go of the microphone.
      if (!wrap.isConnected) {
        end();
        return;
      }
      timer.textContent = `${minutes(Math.floor((Date.now() - started) / 1000))} / 3:00`;
    }, 250);
    limit = window.setTimeout(end, MEMO_STOP_MS);
    record.hidden = true;
    timer.textContent = "0:00 / 3:00";
    timer.hidden = false;
    stop.disabled = false;
    stop.hidden = false;
    stop.focus();
  };
  record.addEventListener("click", () => void startRecording());
  stop.addEventListener("click", () => {
    stop.disabled = true;
    finish?.();
  });
  return wrap;
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
    // A textarea: the model writes one line per note, and a single-line input would drop
    // the line breaks.
    const notes = h("textarea", { attrs: { id: "profile-notes", rows: "3" } });
    notes.value = profile?.notes ?? "";
    const notesLabel = h("label", { text: "Notes", attrs: { for: "profile-notes" } });
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
    // A field is sent only when the person changed its control. Comparing with the profile
    // instead would send any value a control cannot hold as drawn, such as a line break in
    // a single-line input, and mark it person-set.
    const whole = (text: string): number | null => (text.trim() === "" ? null : Number(text));
    const controls: [string, HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement, (text: string) => unknown][] = [
      ["budget_text", budget, (text) => text],
      ["budget_min", min, whole],
      ["budget_max", max, whole],
      ["authority", who, (text) => text],
      ["authority_others", others, (text) => text],
      ["need", need, (text) => text],
      ["timeline_text", timeline, (text) => text],
      ["timeline_month", month, (text) => text || null],
      ["notes", notes, (text) => text],
    ];
    const drawn = new Map(controls.map(([, control]) => [control, control.value]));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const changes: Record<string, unknown> = {};
      for (const [name, control, read] of controls) {
        if (control.value !== drawn.get(control)) changes[name] = read(control.value);
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
