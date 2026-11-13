// Pool, My leads and Unassigned: one lead list each, refreshed in place.

import { api, session, type Lead, type Team } from "../api";
import { h, link, remember } from "../dom";
import { LeadList, type Mode } from "../leadlist";
import { emptyState, loadFailed, poll, skeleton, staleLine } from "../poller";
import { navigate, type Cleanup } from "../router";
import { setTitle } from "../shell";
import { alertsNudge } from "./alerts";

let teamCache: Team[] | null = null;

export async function allTeams(): Promise<Team[]> {
  teamCache ??= await api<Team[]>("GET", "/teams");
  return teamCache;
}

export function forgetTeams(): void {
  teamCache = null;
}

interface ListView {
  heading: string;
  count: (n: number) => string;
  mode: Mode;
  label: (n: number) => string;
  path: string;
  failWhat: string;
  empty: () => HTMLElement;
  before?: HTMLElement[];
  teams?: () => Team[];
}

function leadsView(main: HTMLElement, view: ListView): Cleanup {
  const title = h("h1", { text: view.heading, attrs: { tabindex: "-1" } });
  const count = h("span", { class: "muted num" });
  const stale = staleLine();
  const body = h("div", {}, skeleton());
  const list = new LeadList(view.mode, view.teams ?? (() => []), view.label, () => show());
  const show = (): void => {
    count.textContent = view.count(list.count);
    if (list.count === 0 && list.leads().length === 0) body.replaceChildren(view.empty());
    else if (body.firstChild !== list.element) body.replaceChildren(list.element);
  };
  main.replaceChildren(h("div", { class: "head" }, title, count), ...(view.before ?? []), stale, body);
  let loaded = false;
  const load = async (): Promise<void> => {
    try {
      const leads = await api<Lead[]>("GET", view.path);
      list.update(leads);
      loaded = true;
      show();
    } catch (error) {
      if (!loaded) body.replaceChildren(loadFailed(view.failWhat, () => void poller.now()));
      throw error;
    }
  };
  const poller = poll(load, stale, () => list.tick());
  void poller.now();
  return poller.stop;
}

export function poolPage(main: HTMLElement, params: Record<string, string>): Cleanup {
  const s = session();
  if (!s) return () => undefined;
  const mine = Object.keys(s.user.teams);
  let stopped = false;
  let stop: Cleanup = () => undefined;
  void allTeams().then((teams) => {
    if (stopped) return;
    const ownTeams = teams.filter((t) => mine.includes(t.id));
    const chosen =
      ownTeams.find((t) => t.id === params.teamId) ??
      ownTeams.find((t) => t.id === remember("firstcall.team")) ??
      ownTeams[0];
    if (!chosen) {
      setTitle("Pool");
      main.replaceChildren(h("h1", { text: "Pool" }), emptyState("You're not in a team yet.", "Ask your admin to add you to one."));
      return;
    }
    remember("firstcall.team", chosen.id);
    const owned = new URLSearchParams(window.location.search).get("owned") === "1";
    const leads = s.user.teams[chosen.id] === "team_lead";
    setTitle(owned ? `Owned in ${chosen.name}` : `${chosen.name} pool`);
    const before: HTMLElement[] = [];
    if (ownTeams.length > 1) before.push(...teamPicker(ownTeams, chosen, owned));
    if (leads) {
      before.push(
        h(
          "nav",
          { class: "teamtabs viewtabs", attrs: { "aria-label": "Pool or owned" } },
          tab(`/pool/${chosen.id}`, "Waiting", !owned),
          tab(`/pool/${chosen.id}?owned=1`, `Owned in ${chosen.name}`, owned),
        ),
      );
    }
    if (!owned) before.push(alertsNudge());
    stop = leadsView(main, {
      heading: owned ? `Owned in ${chosen.name}` : `${chosen.name} pool`,
      count: (n) => (owned ? `${n} open` : `${n} waiting`),
      mode: owned ? "team" : "pool",
      label: (n) => `${chosen.name} ${owned ? "owned leads" : "pool"}, ${n} lead${n === 1 ? "" : "s"}`,
      path: owned ? `/leads?view=team&team_id=${chosen.id}` : `/leads?view=pool&team_id=${chosen.id}`,
      failWhat: owned ? "the team's leads" : "the pool",
      empty: () =>
        owned
          ? emptyState(`No one owns a lead in ${chosen.name}.`, "Claimed leads show here with their owners.")
          : emptyState(`No one is waiting in ${chosen.name}.`, "New leads appear here the moment they arrive."),
      before,
    });
  });
  return () => {
    stopped = true;
    stop();
  };
}

function tab(href: string, text: string, currentPage: boolean): HTMLAnchorElement {
  return h("a", { text, attrs: { href, "data-link": true, "aria-current": currentPage ? "page" : undefined } });
}

function teamPicker(teams: Team[], chosen: Team, owned: boolean): HTMLElement[] {
  const suffix = owned ? "?owned=1" : "";
  const select = h("select", { attrs: { id: "team-picker" } });
  for (const team of teams) {
    select.append(h("option", { text: team.name, attrs: { value: team.id, selected: team.id === chosen.id } }));
  }
  select.addEventListener("change", () => navigate(`/pool/${select.value}${suffix}`));
  const picker = h(
    "div",
    { class: "teamselect" },
    h("label", { text: "Team", attrs: { for: "team-picker" } }),
    select,
  );
  if (teams.length > 5) return [picker];
  picker.classList.add("phone-only");
  return [
    h(
      "nav",
      { class: "teamtabs", attrs: { "aria-label": "Teams" } },
      ...teams.map((t) => tab(`/pool/${t.id}${suffix}`, t.name, t.id === chosen.id)),
    ),
    picker,
  ];
}

export function minePage(main: HTMLElement): Cleanup {
  setTitle("My leads");
  return leadsView(main, {
    heading: "My leads",
    count: (n) => `${n} open`,
    mode: "mine",
    label: (n) => `My leads, ${n}`,
    path: "/leads?view=mine",
    failWhat: "your leads",
    empty: () =>
      emptyState("You don't own any leads yet.", "Claim one from your team's pool.", link("/pool", "Go to pool")),
  });
}

export function unassignedPage(main: HTMLElement): Cleanup {
  setTitle("Unassigned");
  let stopped = false;
  let stop: Cleanup = () => undefined;
  main.replaceChildren(h("h1", { text: "Unassigned" }), skeleton());
  void allTeams()
    .then((teams) => {
      if (stopped) return;
      stop = leadsView(main, {
        heading: "Unassigned",
        count: (n) => `${n} need${n === 1 ? "s" : ""} a team`,
        mode: "unassigned",
        label: (n) => `Unassigned, ${n} lead${n === 1 ? "" : "s"}`,
        path: "/leads?view=unassigned",
        failWhat: "Unassigned",
        empty: () =>
          emptyState("Nothing is unassigned.", "Leads with no team land here. Right now every lead has one."),
        teams: () => teams,
      });
    })
    .catch(() => main.replaceChildren(loadFailed("Unassigned", () => unassignedPage(main))));
  return () => {
    stopped = true;
    stop();
  };
}
