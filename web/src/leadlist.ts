// The lead row and the list that keeps rows in place across refreshes. A refresh adds and
// removes rows but never rebuilds one that is still there, so keyboard focus and a button
// that is mid-claim are never lost.

import { api, ApiError, type Lead, type Team } from "./api";
import { announce, h, link } from "./dom";
import {
  agoText,
  minutesSince,
  shortTime,
  urgency,
  URGENCY_WORD,
  waitText,
  waitWords,
  type Urgency,
} from "./format";
import { icon, type IconName } from "./icons";
import { toast } from "./toast";

export type Mode = "pool" | "mine" | "team" | "unassigned" | "waiting";

const STATE_WORD: Record<string, string> = {
  pool: "In pool",
  owned: "Owned",
  nurture: "Nurture",
  won: "Won",
  lost: "Lost",
  not_real: "Not real",
};
const URGENCY_ICON: Partial<Record<Urgency, IconName>> = {
  waiting: "clock",
  late: "triangle",
  verylate: "octagon",
};
// A lost claim stays on screen at least this long, so it never vanishes under the reader.
const LOST_HOLD_MS = 5000;

export function stateBadge(state: string): HTMLElement {
  return h("span", { class: "badge", text: STATE_WORD[state] ?? state });
}

export function leadName(lead: Lead, href?: string): HTMLElement {
  if (!lead.name) return h("span", { class: "name none", text: "No name given" });
  return href ? link(href, lead.name, "name") : h("span", { class: "name", text: lead.name });
}

/** When the lead's current pool stay began. Waits, urgency and escalation all count from
 * here; time to claim alone counts from arrival. */
export function stayStart(lead: Lead): string {
  return lead.pool_since ?? lead.arrival_at;
}

export function leadUrgency(lead: Lead): Urgency {
  return urgency(lead.wait_business_minutes, minutesSince(stayStart(lead)), lead.thresholds);
}

/** The wait timer with its urgency word and icon. */
export function waitTimer(lead: Lead): HTMLElement {
  const box = h("div", { class: "timer" });
  fillTimer(box, lead);
  return box;
}

function fillTimer(box: HTMLElement, lead: Lead): void {
  const minutes = minutesSince(stayStart(lead));
  const level = leadUrgency(lead);
  box.className = `timer u-${level}`;
  box.setAttribute("aria-label", `${waitWords(minutes)}. ${URGENCY_WORD[level]}.`);
  const glyph = URGENCY_ICON[level];
  const badge = h("span", { class: "badge u" }, glyph ? icon(glyph) : null, URGENCY_WORD[level]);
  box.replaceChildren(h("span", { class: "t num", text: waitText(minutes) }), badge);
}

interface Entry {
  li: HTMLLIElement;
  lead: Lead;
  lostAt?: number;
}

export class LeadList {
  readonly element: HTMLUListElement;
  private entries = new Map<string, Entry>();

  constructor(
    private readonly mode: Mode,
    private readonly teams: () => Team[],
    private readonly label: (count: number) => string,
    private readonly onChange: () => void = () => undefined,
  ) {
    this.element = h("ul", { class: "leads" });
  }

  get count(): number {
    return [...this.entries.values()].filter((entry) => entry.lostAt === undefined).length;
  }

  leads(): Lead[] {
    return [...this.entries.values()].map((entry) => entry.lead);
  }

  update(leads: Lead[]): void {
    const now = Date.now();
    const wanted = new Set(leads.map((lead) => lead.id));
    for (const [id, entry] of this.entries) {
      const held = entry.lostAt !== undefined && now - entry.lostAt < LOST_HOLD_MS;
      if (!wanted.has(id) && !held) {
        if (entry.li.contains(document.activeElement)) this.focusAfter(entry.li);
        entry.li.remove();
        this.entries.delete(id);
      }
    }
    let previous: HTMLLIElement | null = null;
    for (const lead of leads) {
      let entry = this.entries.get(lead.id);
      if (!entry) {
        entry = { li: this.row(lead), lead };
        this.entries.set(lead.id, entry);
      } else if (entry.lostAt === undefined) {
        entry.lead = lead;
        this.refreshRow(entry);
      }
      const after: ChildNode | null = previous ? previous.nextSibling : this.element.firstChild;
      if (after !== entry.li) this.element.insertBefore(entry.li, after);
      previous = entry.li;
    }
    this.element.setAttribute("aria-label", this.label(this.count));
  }

  /** Move the timers on without asking the server. */
  tick(): void {
    for (const entry of this.entries.values()) {
      if (entry.lostAt === undefined) this.refreshRow(entry);
    }
  }

  private refreshRow(entry: Entry): void {
    const timer = entry.li.querySelector<HTMLElement>(".timer[data-wait]");
    if (timer) fillTimer(timer, entry.lead);
    const where = entry.li.querySelector<HTMLElement>(".where");
    if (where) where.replaceChildren(...this.whereParts(entry.lead));
  }

  private waits(): boolean {
    return this.mode === "pool" || this.mode === "unassigned" || this.mode === "waiting";
  }

  private whereParts(lead: Lead): string[] {
    const parts = [lead.team?.name ?? "Unassigned", `Arrived ${shortTime(lead.arrival_at)}`];
    const back = lead.pool_since && Date.parse(lead.pool_since) - Date.parse(lead.arrival_at) > 60000;
    if (lead.state === "pool" && back && lead.pool_since) {
      parts.push(`Back in pool ${agoText(lead.pool_since)}`);
    }
    if (lead.state !== "pool" && lead.owner && this.mode === "team") parts.push(lead.owner.name);
    return [parts.join(" · ")];
  }

  private row(lead: Lead): HTMLLIElement {
    const from = this.mode === "mine" ? "mine" : this.mode;
    const first = this.waits() || lead.state === "pool"
      ? (() => {
          const timer = waitTimer(lead);
          timer.dataset.wait = "1";
          return timer;
        })()
      : h(
          "div",
          { class: "timer" },
          stateBadge(lead.state),
          h("span", { class: "meta num", text: lead.claimed_at ? `Claimed ${agoText(lead.claimed_at)}` : "" }),
        );
    const notices = lead.possible_duplicates.length
      ? h("span", { class: "badge" }, icon("copy"), "Possible duplicate")
      : null;
    const who = h(
      "div",
      { class: "who" },
      leadName(lead, `/leads/${lead.id}?from=${from}`),
      h("div", { class: "meta" }, lead.item ?? "No details yet", notices ? " " : null, notices),
    );
    const where = h("div", { class: "where meta num" }, ...this.whereParts(lead));
    const act = h("div", { class: "act" });
    const li = h("li", { class: "row" }, first, who, where, act);
    if (this.mode === "pool" && lead.actions.includes("claim")) {
      act.append(this.claimButton(li, lead));
    }
    if (this.mode === "unassigned" && lead.actions.includes("place")) {
      li.classList.add("wide-act");
      act.append(...this.placeControls(li, lead));
    }
    return li;
  }

  private claimButton(li: HTMLLIElement, lead: Lead): HTMLButtonElement {
    const button = h("button", { class: "btn primary claim", text: "Claim", attrs: { type: "button" } });
    button.setAttribute("aria-label", `Claim ${lead.name ?? "this lead"}`);
    button.addEventListener("click", () => void this.claim(li, lead, button));
    return button;
  }

  private async claim(li: HTMLLIElement, lead: Lead, button: HTMLButtonElement): Promise<void> {
    li.querySelector(".row-error")?.remove();
    busy(button, "Claiming…");
    try {
      await api("POST", `/leads/${lead.id}/claim`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const text = error.detail === "already claimed" ? "Already claimed by a teammate" : "This lead has moved on.";
        this.markLost(li, lead, text);
      } else if (error instanceof ApiError && error.status === 404) {
        this.leave(li, lead);
        toast("This lead is no longer in this pool.");
      } else {
        unbusy(button, "Claim");
        li.append(h("p", { class: "row-error", text: "Couldn't claim. Check your connection and try again." }));
      }
      return;
    }
    li.classList.add("won");
    toast("You own this lead now.", "Open lead", `/leads/${lead.id}?from=mine`);
    announce("You own this lead now.");
    window.setTimeout(() => this.leave(li, lead), reducedMotion() ? 0 : 200);
  }

  private placeControls(li: HTMLLIElement, lead: Lead): HTMLElement[] {
    const id = `place-${lead.id}`;
    const select = h("select", { attrs: { id } });
    for (const team of this.teams()) select.append(h("option", { text: team.name, attrs: { value: team.id } }));
    const button = h("button", { class: "btn primary", text: "Place", attrs: { type: "button" } });
    button.addEventListener("click", () => void this.place(li, lead, select, button));
    return [h("label", { class: "sr", text: `Team for ${lead.name ?? "this lead"}`, attrs: { for: id } }), select, button];
  }

  private async place(
    li: HTMLLIElement,
    lead: Lead,
    select: HTMLSelectElement,
    button: HTMLButtonElement,
  ): Promise<void> {
    li.querySelector(".row-error")?.remove();
    const team = this.teams().find((t) => t.id === select.value);
    busy(button, "Placing…");
    try {
      await api("POST", `/leads/${lead.id}/place`, { team_id: select.value });
    } catch (error) {
      if (error instanceof ApiError && (error.status === 409 || error.status === 404)) {
        this.markLost(li, lead, "Already placed by someone else.");
      } else {
        unbusy(button, "Place");
        li.append(h("p", { class: "row-error", text: "Couldn't place. Check your connection and try again." }));
      }
      return;
    }
    const message = `Placed in ${team?.name ?? "the team"}.`;
    toast(message);
    announce(message);
    this.leave(li, lead);
  }

  private markLost(li: HTMLLIElement, lead: Lead, text: string): void {
    const entry = this.entries.get(lead.id);
    if (entry) entry.lostAt = Date.now();
    li.classList.add("lost");
    const timer = li.querySelector<HTMLElement>(".timer");
    timer?.querySelector(".badge")?.replaceWith(h("span", { class: "badge" }, icon("lock"), "Already claimed"));
    timer?.removeAttribute("data-wait");
    const act = li.querySelector<HTMLElement>(".act");
    // The busy button is disabled, which drops focus to the body, so that counts as ours.
    const hadFocus = (act?.contains(document.activeElement) ?? false) || document.activeElement === document.body;
    const gone = h("span", { class: "gone", attrs: { tabindex: "-1" } }, icon("lock"), text);
    act?.replaceChildren(gone);
    announce(text);
    if (hadFocus) gone.focus();
    this.element.setAttribute("aria-label", this.label(this.count));
    this.onChange();
  }

  private leave(li: HTMLLIElement, lead: Lead): void {
    const hadFocus = li.contains(document.activeElement) || document.activeElement === document.body;
    if (hadFocus) this.focusAfter(li);
    this.entries.delete(lead.id);
    const remove = (): void => {
      li.remove();
      this.element.setAttribute("aria-label", this.label(this.count));
      this.onChange();
    };
    if (reducedMotion()) {
      remove();
      return;
    }
    li.style.maxHeight = `${li.offsetHeight}px`;
    void li.offsetHeight;
    li.classList.add("leaving");
    window.setTimeout(remove, 200);
  }

  private focusAfter(li: HTMLLIElement): void {
    let next = li.nextElementSibling;
    while (next) {
      const target = next.querySelector<HTMLElement>(".act button, .act select");
      if (target) {
        target.focus();
        return;
      }
      next = next.nextElementSibling;
    }
    document.querySelector<HTMLElement>("main h1")?.focus();
  }
}

export function busy(button: HTMLButtonElement, label: string): void {
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.style.minWidth = `${button.offsetWidth}px`;
  button.replaceChildren(icon("loader", false, "spin"), label);
}

export function unbusy(button: HTMLButtonElement, label: string): void {
  button.disabled = false;
  button.removeAttribute("aria-busy");
  button.replaceChildren(label);
}

function reducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
