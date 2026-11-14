"""Outcomes: the reasons a lost lead can give, and who may log a contact.

Lost reasons are a fixed list so the dashboard can group them; "other" carries its own text.
"""

from __future__ import annotations

from typing import Final

from firstcall.core.access import LeadRef, Viewer, relations
from firstcall.core.states import OWNED

OUTCOMES: Final[tuple[str, ...]] = ("won", "lost", "not_real", "nurture")
CLOSED_OUTCOMES: Final[tuple[str, ...]] = ("won", "lost", "not_real")
LOST_REASONS: Final[tuple[str, ...]] = (
    "price",
    "location",
    "timing",
    "bought_elsewhere",
    "not_ready",
    "unreachable",
    "other",
)


def check_outcome(outcome: str, reason: str | None, reason_text: str | None) -> None:
    """Raises ValueError for an outcome the definitions do not allow: lost needs a reason
    from the list, and "other" needs its text."""
    if outcome not in CLOSED_OUTCOMES:
        raise ValueError(f"outcome must be one of {CLOSED_OUTCOMES}")
    if outcome != "lost":
        if reason is not None:
            raise ValueError("only a lost lead takes a reason")
        return
    if reason not in LOST_REASONS:
        raise ValueError("a lost lead needs a reason from the list")
    if reason == "other" and not (reason_text or "").strip():
        raise ValueError("the reason 'other' needs its text")


def may_note(viewer: Viewer, lead: LeadRef) -> bool:
    """The owner, a team lead of the lead's team, or an admin. A teammate who can see a
    pool lead, and a monitor, may not."""
    return bool(relations(viewer, lead) & {"owner", "team_lead", "admin"})


def may_log_contact(viewer: Viewer, lead: LeadRef) -> bool:
    """The same people, and only on an owned or nurture lead: a contact is between the
    lead's owner side and the buyer."""
    return lead.state in OWNED and may_note(viewer, lead)


def may_read_notes(viewer: Viewer, lead: LeadRef) -> bool:
    """Notes, contacts, the profile, outcomes and follow-ups are for the lead's team and
    admins. A monitor sees the lead itself but none of these."""
    return bool(relations(viewer, lead) & {"owner", "team_member", "team_lead", "admin"})
