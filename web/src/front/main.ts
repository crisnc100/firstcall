import "../styles/rail-tokens.css";
import "./front.css";

import { h } from "../dom";
import { URGENCY_WORD, urgency, waitWords, type Urgency } from "../format";
import { sourceLabel } from "../labels";
import { ACTION_WORD } from "../pages/work";
// Copied verbatim by tests/test_front_page.py's source check, which fails if this file drifts
// from its sources:
// - tickets: evals/gold/seed-13/messages.jsonl and labels.jsonl (s13-0006, s13-0004,
//   s13-0058, s13-0012). The confirm run read all 500 seed-13 messages right, these included.
// - note: evals/gold/notes/seed-11/scripts.jsonl (note-11-140) and the reader's recorded
//   answer to it in evals/recordings/notes-claude-haiku-5-5.jsonl.
import examples from "./examples.json";

interface Ticket {
  message: { id: string; source: string; body: string };
  labels: {
    name: string | null;
    phone: string | null;
    email: string | null;
    item: string | null;
    team: string | null;
    agent: { name: string | null; phone: string | null; email: string | null } | null;
  };
}

// Minutes each ticket has already waited when the page opens. Each of the last three crosses
// into its next label within seconds, so a visitor sees a stripe change without waiting.
const STARTS = [0, 4 + 50 / 60, 14 + 45 / 60, 62 + 10 / 60];

const opened = Date.now();
const claimedAt: (number | null)[] = STARTS.map(() => null);
const tickets = examples.tickets as Ticket[];
const status = document.getElementById("claim-status") as HTMLElement;
const painters: (() => void)[] = [];

function waited(index: number): number {
  return claimedAt[index] ?? STARTS[index] + (Date.now() - opened) / 60000;
}

/** "0:42", "14:59", "1:02:10": a kitchen timer, not the app's rounded "14 min". */
function clock(minutes: number): string {
  const seconds = Math.floor(minutes * 60);
  const pad = (n: number): string => String(n).padStart(2, "0");
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** The app's own thresholds: with no team figures it uses the defaults its teams start
 * with (Waiting at 5, Late at 15, Very late at 60). */
function ageOf(minutes: number): Urgency {
  return urgency(null, minutes, null);
}

function rawLines(body: string): string {
  const [head, ...rest] = body.split("\n\n");
  const kept = head.split("\n").filter((line) => line.startsWith("From:") || line.startsWith("Subject:"));
  return `${kept.join("\n")}\n\n${rest.join("\n\n").trimEnd()}`;
}

function field(term: string, value: string | null): HTMLElement[] {
  return [h("dt", { text: term }), h("dd", { text: value ?? "Not given" })];
}

function ticketCard(ticket: Ticket, index: number): HTMLLIElement {
  const { labels, message } = ticket;
  const first = index === 0;
  const timer = h("span", { class: "t-timer", attrs: { "aria-hidden": "true" } });
  const word = h("span", { class: "age-word" });
  const spoken = h("span", { class: "sr" });
  const head = h(
    "header",
    { class: "t-head" },
    h("span", { class: "t-src" }, sourceLabel(message.source), h("span", { class: "dot", text: " · " }), labels.team ?? "unassigned"),
    timer,
  );
  const age = h("p", { class: "t-age" }, word, spoken);
  const fields = h(
    "dl",
    { class: "fields" },
    ...field("Buyer", labels.name),
    ...(first ? [...field("Phone", labels.phone), ...field("Email", labels.email)] : []),
    ...field("Wants", labels.item),
    ...(first && labels.agent ? field("Agent", `${labels.agent.name} ${labels.agent.phone}`) : []),
    ...(first ? field("Team", labels.team) : []),
  );
  const name = labels.name ?? "this";
  const key = h("button", {
    class: "heard",
    text: first ? "Heard — Claim" : "Heard",
    attrs: { type: "button", "aria-label": `Heard: claim ${name}'s lead` },
  });
  const body: HTMLElement[] = [h("div", { class: "stripe" }), head, age];
  if (first) {
    body.push(
      h(
        "div",
        { class: "split" },
        h(
          "div",
          { class: "received" },
          h("p", { class: "t-label", text: "Received" }),
          h("pre", { class: "raw", text: rawLines(message.body) }),
        ),
        h(
          "div",
          { class: "printed" },
          h("p", { class: "t-label", text: "Printed by the reader" }),
          fields,
          h("p", {
            class: "t-aside",
            text: "Two phone numbers in this email. The buyer's went on the ticket; the agent's stayed with the agent.",
          }),
        ),
      ),
    );
  } else {
    body.push(fields);
  }
  body.push(key);
  const card = h("li", { class: first ? "ticket big" : "ticket", attrs: { "data-id": message.id } }, ...body);

  key.addEventListener("click", () => {
    claimedAt[index] = waited(index);
    const stamp = h("p", {
      class: "claimed",
      text: `Claimed by you · ${clock(claimedAt[index] ?? 0)}`,
      attrs: { tabindex: "-1" },
    });
    key.replaceWith(stamp);
    stamp.focus();
    status.textContent = `You claimed ${name}'s lead. That is the only change: the timer stopped, and nothing was sent to the buyer.`;
    paint();
  });

  const paint = (): void => {
    const minutes = waited(index);
    const claimed = claimedAt[index] !== null;
    const state = ageOf(minutes);
    card.dataset.age = claimed ? "claimed" : state;
    timer.textContent = clock(minutes);
    word.textContent = claimed ? "Claimed" : URGENCY_WORD[state];
    spoken.textContent = `, ${waitWords(minutes).toLowerCase()}`;
  };
  painters.push(paint);
  return card;
}

function renderRail(): void {
  const list = document.getElementById("tickets") as HTMLOListElement;
  list.replaceChildren(...tickets.map(ticketCard));
  const tick = (): void => painters.forEach((paint) => paint());
  tick();
  window.setInterval(tick, 1000);
}

function money(value: number): string {
  return `$${value.toLocaleString("en-US")}`;
}

function renderNote(): void {
  const { script, answer } = examples.note;
  const date = new Date(`${script.note_date}T12:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
  const typed = h(
    "figure",
    { class: "slip typed" },
    h("figcaption", { class: "t-label", text: `As typed · ${date}` }),
    h("p", { class: "print", text: script.text }),
  );
  const profile = h("dl", { class: "fields" });
  if (answer.budget && answer.budget.min !== null && answer.budget.max !== null) {
    const range =
      answer.budget.min === answer.budget.max
        ? money(answer.budget.min)
        : `${money(answer.budget.min)} to ${money(answer.budget.max)}`;
    profile.append(...field("Budget", range));
  }
  if (answer.need) profile.append(...field("Needs", answer.need));

  const suggestions = answer.actions.map((action) => {
    const verb = ACTION_WORD[action.kind] ?? action.kind;
    const result = h("p", { class: "result", attrs: { role: "status" } });
    const keys = h("div", { class: "keys" });
    const choose = (label: string, outcome: string): HTMLButtonElement => {
      const button = h("button", {
        class: "heard",
        text: label,
        attrs: { type: "button", "aria-label": `${label} the suggestion: ${verb.toLowerCase()}` },
      });
      button.addEventListener("click", () => {
        keys.remove();
        result.textContent = outcome;
        result.setAttribute("tabindex", "-1");
        result.focus();
      });
      return button;
    };
    keys.append(
      choose("Confirm", `Logged: ${verb} ${examples.note.labels.buyer}. Saved only because you pressed Confirm.`),
      choose("Dismiss", "Dismissed. Nothing was logged."),
    );
    return h(
      "div",
      { class: "suggestion" },
      h("p", { class: "t-label", text: "Suggested contact" }),
      h("p", { class: "print" }, `${verb}: “${action.quote}”`),
      keys,
      result,
    );
  });

  const read = h(
    "figure",
    { class: "slip read" },
    h("figcaption", { class: "t-label", text: "As read" }),
    h("p", { class: "print", text: answer.clean_note }),
    profile,
    ...suggestions,
    h("p", {
      class: "t-aside",
      text: "“Will call Charles tomorrow” gets no suggestion: it hasn't happened yet.",
    }),
  );
  (document.getElementById("note-pair") as HTMLElement).replaceChildren(typed, read);
}

renderRail();
renderNote();
