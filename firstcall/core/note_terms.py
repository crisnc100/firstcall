"""The note reader's vocabulary, fixed in ADR-007 before any note gold or reader code.

The note gold, the scorer and the reader's answer schema all take their values from here, so
none of them can drift from the others.
"""

from __future__ import annotations

from typing import Final, Literal

Authority = Literal["decision_maker", "shares_decision", "not_decision_maker", "unknown"]
ActionKind = Literal["call", "text", "email", "meeting"]

AUTHORITIES: Final[tuple[Authority, ...]] = (
    "decision_maker",
    "shares_decision",
    "not_decision_maker",
    "unknown",
)
# "unknown" is how an empty authority is written; it never counts as filled.
EMPTY_AUTHORITY: Final[Authority] = "unknown"
ACTION_KINDS: Final[tuple[ActionKind, ...]] = ("call", "text", "email", "meeting")

BANT_FIELDS: Final[tuple[str, ...]] = ("budget", "authority", "need", "timeline")
# Fields of the reader's answer that hold free text, in the order the scorer reads them.
TEXT_FIELDS: Final[tuple[str, ...]] = (
    "budget.text",
    "authority.others",
    "need",
    "timeline.text",
    "notes",
    "clean_note",
)
# Where a key term may be found. Notes and the tidied note never count.
TERM_FIELDS: Final[dict[str, str]] = {
    "budget": "budget.text",
    "authority": "authority.others",
    "need": "need",
    "timeline": "timeline.text",
}
MONTH_FORMAT: Final[str] = "YYYY-MM"

# Hard non-actions: sentences about contact that did not happen, or not by the note's writer.
NON_ACTION_KINDS: Final[tuple[str, ...]] = (
    "future",
    "negation",
    "plan",
    "hypothetical",
    "colleague",
)
