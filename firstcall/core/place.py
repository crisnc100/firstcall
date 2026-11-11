"""Place: which team's pool a message goes to. No team means the Unassigned pool."""

from __future__ import annotations

from collections.abc import Mapping


def team_key(text: str) -> str:
    folded = " ".join(text.casefold().split())
    return folded.removesuffix(" team").strip()


def place(
    item: str | None,
    team_hint: str | None,
    default_team: str | None,
    item_teams: Mapping[str, str],
    team_names: Mapping[str, str],
) -> str | None:
    """The team slug for a message, from its item, then a form's team field, then the
    source's default team. `team_names` maps a team's folded name and slug to its slug."""
    if item and item in item_teams:
        return item_teams[item]
    if team_hint:
        slug = team_names.get(team_key(team_hint))
        if slug:
            return slug
    return default_team
