"""Match and place as pure rules: each rule, each doubtful case, and the category gate (F9)."""

from __future__ import annotations

import pytest

from firstcall.core.match import Candidate, Decision, compatible, decide, gate
from firstcall.core.place import place

ITEMS = {"Juniper Mesa": "west", "Cedar Point Lofts": "north"}
NAMES = {"west": "west", "north": "north"}


def lead(
    lead_id: str,
    team: str | None = "west",
    name: str | None = "ana",
    *,
    email: bool = False,
    phone: bool = False,
) -> Candidate:
    return Candidate(lead_id, team, name, by_email=email, by_phone=phone)


def test_no_candidates_makes_a_new_lead_in_the_placed_team() -> None:
    assert decide("west", "ana", []) == Decision(None, "west")
    assert decide(None, "ana", []) == Decision(None, None)


def test_same_email_attaches() -> None:
    assert decide("west", "bea", [lead("A", email=True)]) == Decision("A", "west")


def test_same_phone_needs_a_compatible_first_name() -> None:
    assert decide("west", "ana", [lead("A", phone=True)]) == Decision("A", "west")
    assert decide("west", None, [lead("A", phone=True)]) == Decision("A", "west")
    nameless = [lead("A", name=None, phone=True)]
    assert decide("west", "ana", nameless) == Decision(None, "west", ("A",))


def test_household_phone_is_a_flagged_new_lead() -> None:
    assert decide("west", "luis", [lead("A", phone=True)]) == Decision(None, "west", ("A",))


def test_phone_to_one_lead_and_email_to_another_never_merges() -> None:
    candidates = [lead("A", phone=True), lead("B", name="bea", email=True)]
    assert decide("west", "ana", candidates) == Decision(None, "west", ("A", "B"))


def test_email_and_phone_on_the_same_lead_attach_even_with_a_household_hit() -> None:
    candidates = [lead("A", email=True, phone=True), lead("H", name="luis", phone=True)]
    assert decide("west", "ana", candidates) == Decision("A", "west")


def test_two_phone_hits_with_compatible_names_are_flagged_not_merged() -> None:
    candidates = [lead("A", phone=True), lead("B", phone=True)]
    assert decide("west", "ana", candidates) == Decision(None, "west", ("A", "B"))


def test_with_no_team_one_hit_attaches_to_its_team_and_two_go_to_unassigned() -> None:
    assert decide(None, "ana", [lead("A", team="north", email=True)]) == Decision("A", "north")
    two = [lead("A", team="north", email=True), lead("B", team="west", email=True)]
    assert decide(None, "ana", two) == Decision(None, None, ("A", "B"))


def test_a_lead_in_another_team_is_not_a_match() -> None:
    assert decide("west", "ana", [lead("A", team="north", email=True)]) == Decision(None, "west")


def test_compatible_names() -> None:
    assert compatible("ana", "ana")
    assert compatible(None, "ana")
    assert not compatible("ana", "")
    assert not compatible("ana", "luis")


@pytest.mark.parametrize("category", ["lead", "vendor", "support", "spam"])
def test_a_known_buyer_attaches_whatever_the_category(category: str) -> None:
    assert gate(Decision("A", "west"), category) == "attach"


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("lead", "create"),
        ("vendor", "not_a_lead"),
        ("support", "not_a_lead"),
        ("spam", "not_a_lead"),
    ],
)
def test_an_unmatched_message_passes_the_category_gate(category: str, expected: str) -> None:
    assert gate(Decision(None, "west"), category) == expected


def test_place_uses_item_then_team_field_then_default() -> None:
    assert place("Juniper Mesa", "North", "north", ITEMS, NAMES) == "west"
    assert place(None, "North Team", None, ITEMS, NAMES) == "north"
    assert place(None, "  west  ", None, ITEMS, NAMES) == "west"
    assert place(None, "Atlantis", "north", ITEMS, NAMES) == "north"
    assert place("Unknown Item", None, None, ITEMS, NAMES) is None
    assert place(None, None, None, ITEMS, NAMES) is None


def test_a_named_message_never_joins_a_nameless_lead_on_phone_alone() -> None:
    nameless = lead("N", name=None, phone=True)
    assert decide("west", "luis", [nameless]) == Decision(None, "west", ("N",))
    assert decide(None, "luis", [nameless]) == Decision(None, None, ("N",))
    assert not compatible("luis", None)


def test_a_nameless_message_still_joins_on_phone() -> None:
    assert decide("west", None, [lead("A", phone=True)]) == Decision("A", "west")
    assert decide("west", None, [lead("N", name=None, phone=True)]) == Decision("N", "west")
    assert compatible(None, None)
    assert compatible("", "ana")


def test_a_named_repeat_is_not_blocked_by_a_nameless_lead_on_the_same_phone() -> None:
    own = lead("L", name="lindsay", email=True, phone=True)
    other = lead("N", team="north", name=None, phone=True)
    assert decide(None, "lindsay", [own, other]) == Decision("L", "west")
