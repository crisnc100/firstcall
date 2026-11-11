"""Match a message to an earlier lead of the same buyer, then the category gate.

A wrong join hides a new buyer inside someone else's lead, which is worse than a duplicate,
so every doubtful case makes a new lead flagged with its possible duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    """A lead that shares an identity with the message."""

    lead_id: str
    team: str | None
    first_name: str | None
    by_email: bool
    by_phone: bool


@dataclass(frozen=True)
class Decision:
    attach_to: str | None
    team: str | None
    possible_duplicates: tuple[str, ...] = ()


def compatible(message_name: str | None, lead_name: str | None) -> bool:
    """Whether a phone hit may join: the message has no first name, or both first names are
    equal. A named message never joins a nameless lead on phone alone, because the rules
    leave a lead nameless whenever they cannot find the name, and a different person on the
    same phone would then join it."""
    if not message_name:
        return True
    return message_name == lead_name


def decide(
    placed_team: str | None, first_name: str | None, candidates: list[Candidate]
) -> Decision:
    pool = candidates if placed_team is None else [c for c in candidates if c.team == placed_team]
    by_email = {c.lead_id for c in pool if c.by_email}
    by_phone = {c.lead_id for c in pool if c.by_phone}
    phone_fits = {c.lead_id for c in pool if c.by_phone and compatible(first_name, c.first_name)}
    if by_email and by_phone and not by_email & by_phone:
        return Decision(None, placed_team, tuple(sorted(by_email | by_phone)))
    hits = by_email | phone_fits
    if len(hits) == 1:
        lead_id = next(iter(hits))
        team = next(c.team for c in pool if c.lead_id == lead_id)
        return Decision(lead_id, team)
    if hits:
        return Decision(None, placed_team, tuple(sorted(hits)))
    if by_phone:
        return Decision(None, placed_team, tuple(sorted(by_phone)))
    return Decision(None, placed_team)


def gate(decision: Decision, category: str) -> str:
    """attach, create or not_a_lead. A known buyer's message attaches whatever its category,
    so a buyer's "thanks" is never dropped; only unmatched non-leads stop here."""
    if decision.attach_to is not None:
        return "attach"
    return "create" if category == "lead" else "not_a_lead"
