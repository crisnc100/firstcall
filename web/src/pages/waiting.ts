// Waiting longest: the leads whose escalations fired and still stand, then a log of the
// escalations that went out. "Show all unclaimed" adds every pool lead across the teams.

import { api, type Lead, type Team } from "../api";
import { h, link } from "../dom";
import { shortTime } from "../format";
import { LeadList, leadUrgency } from "../leadlist";
import { emptyState, loadFailed, poll, skeleton, staleLine } from "../poller";
import type { Cleanup } from "../router";
import { setTitle } from "../shell";
import { allTeams } from "./lists";

interface Fired {
  level: string;
  fired_at: string;
  stay: string;
  current: boolean;
  lead: Lead;
}

const LEVEL: Record<string, string> = {
  team_unclaimed: "Team leads told",
  team_still_unclaimed: "Admins and monitors told",
  unassigned_still: "Admins told: still unassigned",
};

function byArrival(a: Lead, b: Lead): number {
  return Date.parse(a.arrival_at) - Date.parse(b.arrival_at);
}

export function waitingPage(main: HTMLElement): Cleanup {
  setTitle("Waiting longest");
  const showAll = h("input", { attrs: { type: "checkbox", id: "show-all" } });
  const summary = h("p", { class: "num" });
  const stale = staleLine();
  const body = h("div", {}, skeleton());
  const logHeading = h("h2", { text: "Escalations sent" });
  const log = h("ul", { class: "list-plain" });
  const list = new LeadList("waiting", () => [], (n) => `Waiting longest, ${n} leads`);
  main.replaceChildren(
    h("h1", { text: "Waiting longest", attrs: { tabindex: "-1" } }),
    h("p", { class: "muted lede", text: "Unclaimed leads across every team, late ones first." }),
    h("div", { class: "toolbar" }, showAll, h("label", { text: "Show all unclaimed", attrs: { for: "show-all" } })),
    summary,
    stale,
    body,
    logHeading,
    log,
  );
  let teams: Team[] = [];
  let loaded = false;
  const load = async (): Promise<void> => {
    try {
      teams = teams.length ? teams : await allTeams();
      const fired = await api<Fired[]>("GET", "/escalations");
      let leads: Lead[];
      if (showAll.checked) {
        const pools = await Promise.all([
          api<Lead[]>("GET", "/leads?view=unassigned"),
          ...teams.map((t) => api<Lead[]>("GET", `/leads?view=pool&team_id=${t.id}`)),
        ]);
        leads = pools.flat();
      } else {
        const standing = new Map<string, Lead>();
        for (const row of fired) if (row.current) standing.set(row.lead.id, row.lead);
        leads = [...standing.values()];
      }
      leads.sort(byArrival);
      list.update(leads);
      drawSummary(leads);
      drawLog(fired);
      loaded = true;
      if (!leads.length) {
        body.replaceChildren(
          showAll.checked
            ? emptyState("No one is waiting.", "Every pool is empty right now.")
            : emptyState("No one has waited too long.", "Leads show here once their team is told they are late."),
        );
      } else if (body.firstChild !== list.element) {
        body.replaceChildren(list.element);
      }
    } catch (error) {
      if (!loaded) body.replaceChildren(loadFailed("the waiting list", () => void poller.now()));
      throw error;
    }
  };
  const drawSummary = (leads: Lead[]): void => {
    summary.hidden = leads.length === 0;
    const levels = leads.map(leadUrgency);
    const veryLate = levels.filter((l) => l === "verylate").length;
    const late = levels.filter((l) => l === "late").length;
    const teamCount = new Set(leads.map((l) => l.team?.id ?? "unassigned")).size;
    summary.textContent = `${veryLate} very late, ${late} late, across ${teamCount} team${teamCount === 1 ? "" : "s"}.`;
  };
  const drawLog = (fired: Fired[]): void => {
    logHeading.hidden = fired.length === 0;
    log.hidden = fired.length === 0;
    log.replaceChildren(
      ...fired.slice(0, 50).map((row) =>
        h(
          "li",
          {},
          h("time", { class: "muted num", text: shortTime(row.fired_at), attrs: { datetime: row.fired_at } }),
          h("span", { text: LEVEL[row.level] ?? row.level }),
          link(`/leads/${row.lead.id}?from=waiting`, row.lead.name ?? "No name given"),
          h("span", { class: "muted sm", text: row.lead.team?.name ?? "Unassigned" }),
          row.current ? null : h("span", { class: "badge", text: "Handled" }),
        ),
      ),
    );
  };
  showAll.addEventListener("change", () => void poller.now());
  const poller = poll(load, stale, () => list.tick());
  void poller.now();
  return poller.stop;
}
