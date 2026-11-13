"""The access rule: who can see a lead, and how a viewer relates to it.

A lead a viewer cannot see must answer exactly like a lead that does not exist, so callers
turn a False here into 404, never 403.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from firstcall.core.states import Relation

TeamRole = Literal["salesperson", "team_lead"]
OrgRole = Literal["monitor", "admin"]


@dataclass(frozen=True)
class Viewer:
    user_id: str
    org_id: str = ""
    org_roles: frozenset[str] = frozenset()
    # Team id to the viewer's role in that team.
    teams: Mapping[str, str] = field(default_factory=lambda: {})

    @property
    def admin(self) -> bool:
        return "admin" in self.org_roles

    @property
    def monitor(self) -> bool:
        return "monitor" in self.org_roles

    def leads_team(self, team_id: str | None) -> bool:
        return team_id is not None and self.teams.get(team_id) == "team_lead"

    def in_team(self, team_id: str | None) -> bool:
        return team_id is not None and team_id in self.teams


@dataclass(frozen=True)
class LeadRef:
    team_id: str | None
    owner_id: str | None
    state: str


def can_see(viewer: Viewer, lead: LeadRef) -> bool:
    """Admins and monitors see the whole org; the monitor still gets no notes on team leads.
    A team lead sees every lead in their teams. A salesperson sees their teams' pools and the
    leads they own."""
    if viewer.admin or viewer.monitor:
        return True
    if lead.owner_id is not None and lead.owner_id == viewer.user_id:
        return True
    if viewer.leads_team(lead.team_id):
        return True
    return viewer.in_team(lead.team_id) and lead.state == "pool"


def relations(viewer: Viewer, lead: LeadRef) -> frozenset[Relation]:
    """Every role the viewer plays toward this lead; empty when they cannot see it."""
    if not can_see(viewer, lead):
        return frozenset()
    found: set[Relation] = set()
    if lead.owner_id is not None and lead.owner_id == viewer.user_id:
        found.add("owner")
    if viewer.in_team(lead.team_id):
        found.add("team_member")
    if viewer.leads_team(lead.team_id):
        found.add("team_lead")
    if viewer.monitor:
        found.add("monitor")
    if viewer.admin:
        found.add("admin")
    return frozenset(found)


def can_see_team(viewer: Viewer, team_id: str | None) -> bool:
    """Whether the viewer may list a pool: their own teams, or any pool for an admin or
    monitor. The Unassigned pool (None) belongs to admins and monitors."""
    if viewer.admin or viewer.monitor:
        return True
    return viewer.in_team(team_id)
