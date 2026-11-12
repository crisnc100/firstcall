// One lead: read-only, plus exactly the actions the API lists for this viewer.

import { api, ApiError, session, type LeadDetail, type MessageRow, type Team } from "../api";
import { announce, h, link } from "../dom";
import { fullTime, shortTime } from "../format";
import { icon } from "../icons";
import { busy, stateBadge, unbusy, waitTimer } from "../leadlist";
import { loadFailed } from "../poller";
import type { Cleanup } from "../router";
import { setTitle } from "../shell";
import { toast } from "../toast";
import { allTeams } from "./lists";

const BACK: Record<string, [string, (team: string) => string]> = {
  pool: ["/pool", (team) => `Back to ${team} pool`],
  team: ["/pool", (team) => `Back to ${team} pool`],
  mine: ["/mine", () => "Back to My leads"],
  unassigned: ["/unassigned", () => "Back to Unassigned"],
  waiting: ["/waiting", () => "Back to Waiting longest"],
};

const SOURCES: Record<string, string> = {
  inbox: "Email",
  "site-form": "Web form",
  "partner-form": "Partner form",
  calls: "Phone call",
};

function copyButton(value: string, what: string): HTMLButtonElement {
  const button = h("button", { class: "btn secondary", attrs: { type: "button" } }, icon("copy"), "Copy");
  button.setAttribute("aria-label", `Copy ${what}`);
  button.addEventListener("click", () => {
    void navigator.clipboard?.writeText(value).then(() => {
      button.replaceChildren(icon("check"), "Copied");
      announce(`${what} copied`);
      window.setTimeout(() => button.replaceChildren(icon("copy"), "Copy"), 2000);
    });
  });
  return button;
}

function contact(value: string | null, href: string, missing: string, what: string): HTMLElement {
  if (!value) return h("dd", { class: "muted", text: missing });
  return h("dd", { class: "line" }, h("a", { class: "num", text: value, attrs: { href } }), copyButton(value, what));
}

/** The message body, or a plain line when it has none: a form post may carry only fields. */
export function messageText(text: string | null | undefined): HTMLElement {
  if (!text?.trim()) return h("p", { class: "muted", text: "No message text." });
  return h("div", { class: "msg", text });
}

function messageBlock(message: MessageRow): HTMLElement[] {
  const text = messageText(message.text);
  const parts: HTMLElement[] = [
    h(
      "div",
      { class: "line muted sm" },
      `${SOURCES[message.source] ?? message.source} · ${shortTime(message.received_at)}`,
      message.category ? h("span", { class: "badge", text: message.category }) : null,
      h("span", { class: "badge", text: message.status.replace(/_/g, " ") }),
    ),
    text,
  ];
  if ((message.text ?? "").split("\n").length > 12) {
    text.classList.add("clipped");
    const more = h("button", { class: "btn secondary", text: "Show the whole message", attrs: { type: "button" } });
    more.addEventListener("click", () => {
      text.classList.remove("clipped");
      more.remove();
    });
    parts.push(more);
  }
  return parts;
}

function sentence(
  event: LeadDetail["events"][number],
  names: Map<string, string>,
  teams: Team[],
  lead: LeadDetail,
): string {
  const p = event.payload;
  const who = (key: string): string => names.get(String(p[key] ?? "")) ?? "a teammate";
  const team = (value: unknown): string =>
    teams.find((t) => t.id === value || t.slug === value)?.name ?? "another team";
  switch (event.type) {
    case "lead.created": {
      const first = lead.messages[0];
      return `Arrived from ${first ? (SOURCES[first.source] ?? first.source) : "a source"}`;
    }
    case "lead.placed":
      return `Placed in ${team(p.team)}`;
    case "lead.claimed":
      return `Claimed by ${who("owner_id")}`;
    case "lead.released":
      return `Released to the pool by ${who("by")}`;
    case "lead.returned":
      return p.reason === "owner_left"
        ? `Returned to the pool: ${who("owner_id")} left the team`
        : `Returned to the pool by ${who("by")}`;
    case "lead.reassigned":
      return `Reassigned from ${who("from")} to ${who("to")}`;
    case "lead.moved_team":
      return `Moved from ${team(p.from_team)} to ${team(p.to_team)}`;
    case "lead.activity":
      return "New message from the buyer";
    default:
      return event.type.replace(/[._]/g, " ");
  }
}

export function detailPage(main: HTMLElement, params: Record<string, string>): Cleanup {
  const from = new URLSearchParams(window.location.search).get("from") ?? "pool";
  let stopped = false;
  const load = async (): Promise<void> => {
    let lead: LeadDetail;
    try {
      lead = await api<LeadDetail>("GET", `/leads/${params.id}`);
    } catch (error) {
      if (stopped) return;
      if (error instanceof ApiError && error.status === 404) {
        setTitle("Lead not available");
        main.replaceChildren(
          h("h1", { text: "Lead not available" }),
          h("p", { text: "This lead isn't available. It may have moved to a team you're not in." }),
          link(BACK[from]?.[0] ?? "/pool", "Back"),
        );
        return;
      }
      main.replaceChildren(loadFailed("the lead", () => void load()));
      return;
    }
    const teams = await allTeams().catch(() => [] as Team[]);
    if (stopped) return;
    await draw(main, lead, teams, from, load);
  };
  void load();
  return () => {
    stopped = true;
  };
}

async function draw(
  main: HTMLElement,
  lead: LeadDetail,
  teams: Team[],
  from: string,
  reload: () => Promise<void>,
): Promise<void> {
  const s = session();
  const names = new Map<string, string>();
  if (s) names.set(s.user.id, s.user.name);
  if (lead.owner) names.set(lead.owner.id, lead.owner.name);
  let members: { id: string; name: string }[] = [];
  if (lead.team && (lead.actions.includes("reassign") || s?.user.roles.includes("admin"))) {
    members = await api<{ id: string; name: string }[]>("GET", `/teams/${lead.team.id}/members`).catch(() => []);
  }
  for (const member of members) names.set(member.id, member.name);

  const teamName = lead.team?.name ?? "Unassigned";
  setTitle(lead.name ?? "No name given");
  const [backHref, backText] = BACK[from] ?? BACK.pool;
  const header: HTMLElement[] = [
    link(backHref, backText(teamName), "sm"),
    h("h1", { class: "lead-name" }, lead.name ?? "No name given"),
    h(
      "div",
      { class: "line" },
      stateBadge(lead.state),
      h("span", { class: "muted sm", text: `${teamName} · ${lead.owner ? `Owned by ${lead.owner.name}` : "No owner"}` }),
    ),
  ];
  if (!lead.name) header[1].classList.add("none");
  if (lead.state === "pool") {
    header.push(h("div", { class: "line", attrs: { style: "margin-top: 16px" } }, waitTimer(lead)));
  } else if (lead.claim_minutes !== null && lead.claim_business_minutes !== null) {
    header.push(
      h("p", {
        class: "num",
        text: `Claimed in ${Math.round(lead.claim_business_minutes)} business minutes (${Math.round(lead.claim_minutes)} min on the clock)`,
      }),
    );
  }

  const facts = h(
    "dl",
    {},
    h("dt", { text: "Phone" }),
    contact(lead.phone, `tel:${lead.phone ?? ""}`, "No phone given", "phone"),
    h("dt", { text: "Email" }),
    contact(lead.email, `mailto:${lead.email ?? ""}`, "No email given", "email"),
    h("dt", { text: "Asked about" }),
    h("dd", { text: lead.item ?? "No details yet" }),
    h("dt", { text: "Arrived" }),
    h("dd", { class: "num", text: fullTime(lead.arrival_at) }),
  );
  const sent = lead.messages[0]?.source_time;
  if (sent && Math.abs(Date.parse(sent) - Date.parse(lead.arrival_at)) > 60000) {
    facts.append(h("dt", { text: "Sent at (per the message)" }), h("dd", { class: "num", text: fullTime(sent) }));
  }
  if (lead.state === "pool" && lead.pool_since && Date.parse(lead.pool_since) - Date.parse(lead.arrival_at) > 60000) {
    facts.append(h("dt", { text: "Back in pool since" }), h("dd", { class: "num", text: fullTime(lead.pool_since) }));
  }

  const notices: HTMLElement[] = [];
  if (lead.possible_duplicates.length) {
    const box = h(
      "div",
      { class: "notice" },
      icon("info", true),
      h("div", {}, h("p", { text: "This may be the same buyer as another lead.", attrs: { style: "margin: 0" } })),
    );
    notices.push(box);
    for (const id of lead.possible_duplicates) {
      void api<LeadDetail>("GET", `/leads/${id}`)
        .then((other) => box.lastElementChild?.append(h("p", { attrs: { style: "margin: 4px 0 0" } }, link(`/leads/${other.id}?from=${from}`, other.name ?? "No name given"))))
        .catch(() => undefined);
    }
  }
  if (lead.flags.length) {
    notices.push(h("div", { class: "notice" }, icon("info", true), h("p", { text: "Flagged for the monitor to check.", attrs: { style: "margin: 0" } })));
  }

  const messages: HTMLElement[] = [h("h2", { text: `Messages (${lead.messages.length})` })];
  for (const message of lead.messages) messages.push(...messageBlock(message));
  const history = h(
    "ul",
    { class: "history" },
    ...[...lead.events]
      .reverse()
      .map((event) =>
        h("li", {}, h("time", { class: "num", text: shortTime(event.at), attrs: { datetime: event.at } }), h("span", { text: sentence(event, names, teams, lead) })),
      ),
  );

  const column = h("div", {}, ...header, ...notices, facts, ...messages, h("h2", { text: "History" }), history);
  const actions = actionColumn(lead, teams, members, reload);
  main.replaceChildren(h("div", { class: actions ? "detail" : "" }, column, actions));
}

function actionColumn(
  lead: LeadDetail,
  teams: Team[],
  members: { id: string; name: string }[],
  reload: () => Promise<void>,
): HTMLElement | null {
  if (!lead.actions.length) return null;
  const status = h("div", { attrs: { role: "status" } });
  const aside = h("aside", { class: "actions", attrs: { "aria-label": "Actions" } });
  const rest = h("div", { class: "rest", attrs: { style: "flex-direction: column; gap: 8px; display: flex" } });
  const teamName = lead.team?.name ?? "the";

  const run = async (
    button: HTMLButtonElement,
    working: string,
    label: string,
    path: string,
    body: unknown,
    done: string,
  ): Promise<void> => {
    status.replaceChildren();
    busy(button, working);
    try {
      await api("POST", `/leads/${lead.id}/${path}`, body);
    } catch (error) {
      unbusy(button, label);
      let text = `Couldn't ${label.toLowerCase().replace(/…$/, "")}. Check your connection and try again.`;
      if (error instanceof ApiError && error.status === 409) {
        text = error.detail === "already claimed" ? "Already claimed by a teammate" : "This lead has moved on.";
      } else if (error instanceof ApiError && error.status === 422) {
        text = error.detail.charAt(0).toUpperCase() + error.detail.slice(1);
      }
      status.replaceChildren(h("p", { class: "danger-text" }, icon("alert"), text));
      announce(text);
      return;
    }
    toast(done);
    announce(done);
    await reload();
  };

  const simple = (name: string, label: string, working: string, style: string, done: string): HTMLButtonElement => {
    const button = h("button", { class: `btn ${style}`, text: label, attrs: { type: "button" } });
    button.addEventListener("click", () => void run(button, working, label, name, undefined, done));
    return button;
  };

  // An inline panel under its trigger. With options it holds a select; without, it is the
  // confirmation for taking a lead from someone, and its button says the whole sentence.
  const panel = (
    label: string,
    selectLabel: string,
    options: { id: string; name: string }[] | null,
    verb: (choice: string) => string,
    working: string,
    path: string,
    field: string,
    done: (choice: string) => string,
  ): HTMLElement => {
    const style = options === null ? "danger" : "secondary";
    const trigger = h("button", { class: `btn ${style}`, text: label, attrs: { type: "button", "aria-expanded": "false" } });
    const wrap = h("div", {}, trigger);
    let open: HTMLElement | null = null;
    const close = (): void => {
      open?.remove();
      open = null;
      trigger.setAttribute("aria-expanded", "false");
      trigger.focus();
    };
    trigger.addEventListener("click", () => {
      if (open) {
        close();
        return;
      }
      const id = `panel-${path}`;
      const select = h("select", { attrs: { id } }, ...(options ?? []).map((o) => h("option", { text: o.name, attrs: { value: o.id } })));
      const choiceName = (): string => (options ?? []).find((o) => o.id === select.value)?.name ?? "";
      const go = h("button", { class: "btn primary", text: verb(choiceName()), attrs: { type: "button" } });
      select.addEventListener("change", () => {
        go.textContent = verb(choiceName());
      });
      const body = options === null ? undefined : (): unknown => ({ [field]: select.value });
      go.addEventListener("click", () => void run(go, working, verb(choiceName()), path, body?.(), done(choiceName())));
      const cancel = h("button", { class: "btn secondary", text: "Cancel", attrs: { type: "button" }, on: { click: close } });
      const buttons = h("div", { class: "row-buttons" }, go, cancel);
      open = options === null
        ? h("div", { class: "panel" }, buttons)
        : h("div", { class: "panel" }, h("label", { text: selectLabel, attrs: { for: id } }), select, buttons);
      open.addEventListener("keydown", (event) => {
        if (event.key === "Escape") close();
      });
      if (options !== null && !options.length) {
        open.replaceChildren(h("p", { text: "There is no one else to choose." }), cancel);
      }
      wrap.append(open);
      trigger.setAttribute("aria-expanded", "true");
      (options === null ? go : options.length ? select : cancel).focus();
    });
    return wrap;
  };

  const otherTeams = teams.filter((t) => t.id !== lead.team?.id);
  let primary: HTMLElement | null = null;
  const others: HTMLElement[] = [];
  for (const action of lead.actions) {
    if (action === "claim") primary = simple("claim", "Claim", "Claiming…", "primary claim", "You own this lead now.");
    if (action === "release") {
      const button = simple("release", "Release to pool", "Releasing…", "secondary", `Released to ${teamName} pool.`);
      if (primary) others.push(button);
      else primary = button;
    }
    if (action === "return") {
      others.push(
        panel("Return to pool…", "", null, () => `Return to ${teamName} pool`, "Returning…", "return", "", () => `Returned to ${teamName} pool.`),
      );
    }
    if (action === "reassign") {
      const choices = members.filter((m) => m.id !== lead.owner?.id);
      others.push(panel("Reassign…", "New owner", choices, (n) => `Reassign to ${n}`, "Reassigning…", "reassign", "user_id", (n) => `Reassigned to ${n}.`));
    }
    if (action === "move_team") {
      others.push(panel("Move to another team…", "Team", otherTeams, (n) => `Move to ${n}`, "Moving…", "move", "team_id", (n) => `Moved to ${n}.`));
    }
    if (action === "place") {
      const place = panel("Place in a team…", "Team", teams, (n) => `Place in ${n}`, "Placing…", "place", "team_id", (n) => `Placed in ${n}.`);
      if (primary) others.push(place);
      else primary = place;
    }
  }
  if (primary) aside.append(primary);
  if (others.length) {
    const more = h("button", { class: "btn secondary more", text: "More actions", attrs: { type: "button", "aria-expanded": "false" } });
    more.addEventListener("click", () => {
      const isOpen = aside.classList.toggle("open");
      more.setAttribute("aria-expanded", String(isOpen));
    });
    rest.append(...others);
    aside.append(more, rest);
  }
  aside.append(status);
  return aside;
}
