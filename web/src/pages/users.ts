// Users for admins: add, edit roles and teams, set a password, deactivate.

import { api, ApiError, type Lead, type Team, type User } from "../api";
import { announce, h } from "../dom";
import { icon } from "../icons";
import { busy, unbusy } from "../leadlist";
import { loadFailed, skeleton } from "../poller";
import type { Cleanup } from "../router";
import { setTitle } from "../shell";
import { toast } from "../toast";
import { allTeams, forgetTeams } from "./lists";

const TEAM_ROLE: Record<string, string> = { salesperson: "salesperson", team_lead: "team lead" };

function message(error: unknown): string {
  if (error instanceof ApiError && error.status === 422) {
    return error.detail.charAt(0).toUpperCase() + error.detail.slice(1);
  }
  return "Couldn't save. Check your connection and try again.";
}

function teamsText(user: User, teams: Team[]): string {
  const parts = Object.entries(user.teams).map(
    ([id, role]) => `${teams.find((t) => t.id === id)?.name ?? "A team"} (${TEAM_ROLE[role] ?? role})`,
  );
  return parts.join(", ") || "No team";
}

function rolesText(user: User): string {
  return user.roles.map((r) => (r === "admin" ? "Admin" : "Monitor")).join(", ") || "None";
}

function teamFields(teams: Team[], current: Record<string, string>): [HTMLElement, () => Record<string, string>] {
  const selects = teams.map((team) => {
    const id = `team-${team.id}`;
    const select = h(
      "select",
      { attrs: { id } },
      h("option", { text: "Not in this team", attrs: { value: "" } }),
      h("option", { text: "Salesperson", attrs: { value: "salesperson", selected: current[team.id] === "salesperson" } }),
      h("option", { text: "Team lead", attrs: { value: "team_lead", selected: current[team.id] === "team_lead" } }),
    );
    return { team, select, field: h("div", { class: "field" }, h("label", { text: team.name, attrs: { for: id } }), select) };
  });
  const box = h("fieldset", { class: "panel" }, h("legend", { text: "Teams" }), ...selects.map((s) => s.field));
  const read = (): Record<string, string> => {
    const wanted: Record<string, string> = {};
    for (const { team, select } of selects) if (select.value) wanted[team.id] = select.value;
    return wanted;
  };
  return [box, read];
}

function roleFields(current: string[]): [HTMLElement, () => string[]] {
  const boxes = (["monitor", "admin"] as const).map((role) => {
    const input = h("input", { attrs: { type: "checkbox", id: `role-${role}`, checked: current.includes(role) } });
    return { role, input, row: h("div", { class: "line" }, input, h("label", { text: role === "admin" ? "Admin" : "Monitor", attrs: { for: `role-${role}` } })) };
  });
  const box = h("fieldset", { class: "panel" }, h("legend", { text: "Org roles" }), ...boxes.map((b) => b.row));
  return [box, () => boxes.filter((b) => b.input.checked).map((b) => b.role)];
}

export function usersPage(main: HTMLElement): Cleanup {
  setTitle("Users");
  main.replaceChildren(h("h1", { text: "Users" }), skeleton());
  const load = async (): Promise<void> => {
    try {
      const [users, teams] = await Promise.all([api<User[]>("GET", "/users"), allTeams()]);
      draw(main, users, teams, load);
    } catch {
      main.replaceChildren(h("h1", { text: "Users" }), loadFailed("the users", () => void load()));
    }
  };
  void load();
  return () => undefined;
}

function draw(main: HTMLElement, users: User[], teams: Team[], reload: () => Promise<void>): void {
  const editor = h("div", {});
  const add = h("button", { class: "btn primary", text: "Add user", attrs: { type: "button" } });
  add.addEventListener("click", () => {
    editor.replaceChildren(addForm(teams, reload, () => editor.replaceChildren()));
    editor.querySelector<HTMLElement>("input")?.focus();
  });
  const edit = (user: User): void => {
    editor.replaceChildren(editForm(user, teams, reload, () => editor.replaceChildren()));
    editor.scrollIntoView({ block: "nearest" });
    editor.querySelector<HTMLElement>("input")?.focus();
  };
  const editButton = (user: User): HTMLButtonElement => {
    const button = h("button", { class: "btn secondary", text: "Edit", attrs: { type: "button" } });
    button.setAttribute("aria-label", `Edit ${user.name}`);
    button.addEventListener("click", () => edit(user));
    return button;
  };
  const table = h(
    "table",
    { class: "users-table" },
    h(
      "thead",
      {},
      h("tr", {}, ...["Name", "Email", "Roles", "Teams", "Status", ""].map((text) => h("th", { text, attrs: { scope: "col" } }))),
    ),
    h(
      "tbody",
      {},
      ...users.map((user) =>
        h(
          "tr",
          { class: user.active ? "" : "inactive" },
          h("th", { text: user.name, attrs: { scope: "row" } }),
          h("td", { text: user.email }),
          h("td", { text: rolesText(user) }),
          h("td", { text: teamsText(user, teams) }),
          h("td", { text: user.active ? "Active" : "Deactivated" }),
          h("td", {}, editButton(user)),
        ),
      ),
    ),
  );
  const list = h(
    "ul",
    { class: "list-plain users-list", attrs: { "aria-label": "Users" } },
    ...users.map((user) =>
      h(
        "li",
        {},
        h("div", { attrs: { style: "flex: 1" } }, h("div", { class: "name", text: user.name }), h("div", { class: "meta", text: `${user.email} · ${rolesText(user)} · ${user.active ? "Active" : "Deactivated"}` })),
        editButton(user),
      ),
    ),
  );
  main.replaceChildren(h("div", { class: "head" }, h("h1", { text: "Users" }), add), editor, table, list);
}

function field(label: string, input: HTMLInputElement, hint?: string): HTMLElement {
  return h(
    "div",
    { class: "field" },
    h("label", { text: label, attrs: { for: input.id } }, hint ? h("span", { class: "muted sm hint", text: ` ${hint}` }) : null),
    input,
  );
}

function addForm(teams: Team[], reload: () => Promise<void>, close: () => void): HTMLElement {
  const name = h("input", { attrs: { id: "new-name", autocomplete: "off" } });
  const email = h("input", { attrs: { id: "new-email", type: "email", autocomplete: "off" } });
  const password = h("input", { attrs: { id: "new-password", type: "password", autocomplete: "new-password" } });
  const [roles, readRoles] = roleFields([]);
  const [teamBox, readTeams] = teamFields(teams, {});
  const error = h("p", { class: "danger-text", attrs: { hidden: true, role: "alert" } });
  const save = h("button", { class: "btn primary", text: "Add user", attrs: { type: "submit" } });
  const form = h(
    "form",
    { class: "panel" },
    h("h2", { text: "Add user", attrs: { style: "margin: 0" } }),
    field("Name", name),
    field("Email", email),
    field("Password", password, "Leave blank to set it later"),
    roles,
    teamBox,
    error,
    h("div", { class: "row-buttons" }, save, h("button", { class: "btn secondary", text: "Cancel", attrs: { type: "button" }, on: { click: close } })),
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    error.hidden = true;
    busy(save, "Adding…");
    api("POST", "/users", {
      name: name.value,
      email: email.value,
      roles: readRoles(),
      teams: readTeams(),
      password: password.value || null,
    })
      .then(async () => {
        password.value = "";
        toast("User added.");
        forgetTeams();
        await reload();
      })
      .catch((caught: unknown) => {
        unbusy(save, "Add user");
        error.hidden = false;
        error.replaceChildren(icon("alert"), message(caught));
      });
  });
  return form;
}

function editForm(user: User, teams: Team[], reload: () => Promise<void>, close: () => void): HTMLElement {
  const name = h("input", { attrs: { id: "edit-name", value: user.name, autocomplete: "off" } });
  const [roles, readRoles] = roleFields(user.roles);
  const [teamBox, readTeams] = teamFields(teams, user.teams);
  const error = h("p", { class: "danger-text", attrs: { hidden: true, role: "alert" } });
  const save = h("button", { class: "btn primary", text: "Save", attrs: { type: "submit" } });
  const extra = h("div", {});

  const password = h("button", { class: "btn secondary", text: "Set a new password", attrs: { type: "button" } });
  password.addEventListener("click", () => {
    const input = h("input", { attrs: { id: "set-password", type: "password", autocomplete: "new-password" } });
    const set = h("button", { class: "btn primary", text: "Set password", attrs: { type: "button" } });
    set.addEventListener("click", () => {
      busy(set, "Setting…");
      api("PUT", `/users/${user.id}/password`, { password: input.value })
        .then(() => {
          input.value = "";
          extra.replaceChildren();
          toast("Password set.");
          password.focus();
        })
        .catch((caught: unknown) => {
          unbusy(set, "Set password");
          error.hidden = false;
          error.replaceChildren(icon("alert"), message(caught));
        });
    });
    extra.replaceChildren(h("div", { class: "panel" }, field("New password", input), h("div", { class: "row-buttons" }, set)));
    input.focus();
  });

  const status = h("button", {
    class: user.active ? "btn danger" : "btn secondary",
    text: user.active ? "Deactivate user" : "Reactivate",
    attrs: { type: "button" },
  });
  status.addEventListener("click", () => {
    if (!user.active) {
      void change({ active: true }, "User reactivated.");
      return;
    }
    void openCount().then((count) => {
      const what = count === null ? "their open leads" : `their ${count} open lead${count === 1 ? "" : "s"}`;
      const confirm = h("button", { class: "btn danger", text: "Deactivate", attrs: { type: "button" } });
      confirm.addEventListener("click", () => void change({ active: false }, `${user.name} deactivated.`));
      const cancel = h("button", { class: "btn secondary", text: "Cancel", attrs: { type: "button" }, on: { click: () => { extra.replaceChildren(); status.focus(); } } });
      extra.replaceChildren(
        h("div", { class: "panel" }, h("p", { text: `Deactivating ${user.name} returns ${what} to their pools.` }), h("div", { class: "row-buttons" }, confirm, cancel)),
      );
      confirm.focus();
    });
  });

  const openCount = async (): Promise<number | null> => {
    try {
      const owned = await Promise.all(
        Object.keys(user.teams).map((id) => api<Lead[]>("GET", `/leads?view=team&team_id=${id}`)),
      );
      return owned.flat().filter((lead) => lead.owner?.id === user.id).length;
    } catch {
      return null;
    }
  };

  const change = async (body: Record<string, unknown>, done: string): Promise<void> => {
    error.hidden = true;
    try {
      await api("PATCH", `/users/${user.id}`, body);
      toast(done);
      announce(done);
      await reload();
    } catch (caught) {
      error.hidden = false;
      error.replaceChildren(icon("alert"), message(caught));
    }
  };

  const form = h(
    "form",
    { class: "panel" },
    h("h2", { text: `Edit ${user.name}`, attrs: { style: "margin: 0" } }),
    h("p", { class: "muted", text: user.email }),
    field("Name", name),
    roles,
    teamBox,
    error,
    h("div", { class: "row-buttons" }, save, password, h("button", { class: "btn secondary", text: "Cancel", attrs: { type: "button" }, on: { click: close } })),
    extra,
    h("div", { class: "row-buttons" }, status),
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(save, "Saving…");
    void change({ name: name.value, roles: readRoles(), teams: readTeams() }, "Saved.").finally(() => unbusy(save, "Save"));
  });
  return form;
}
