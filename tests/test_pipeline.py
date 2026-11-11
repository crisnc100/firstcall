"""The pipeline end to end against Postgres: create, attach, re-forward, timers, matching
rules and the category gate (F3, F7, F9, F10)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import Activity, Identity, Job, Lead, LeadEvent, Message
from tests.conftest import FakeClock
from tests.helpers import Pipeline, email

PORTAL = (
    "New lead from Hearthly\n\nName: Ana Ruiz\nPhone: (202) 555-0142\n"
    "Email: ana.ruiz@inbox.example\nListing: Cedar Point Lofts\n\nMessage:\nIs it available?"
)


@pytest.fixture
def pipe(sessions: sessionmaker[Session], clock: FakeClock) -> Pipeline:
    return Pipeline(sessions, clock)


def portal(body: str = PORTAL, **kwargs: str) -> bytes:
    return email("Hearthly <leads@hearthly.example>", body, "Hearthly: new inquiry", **kwargs)


def site(
    name: str, phone: str = "", mail: str = "", listing: str = "", **extra: str
) -> dict[str, str]:
    return {
        "name": name,
        "phone": phone,
        "email": mail,
        "listing": listing,
        "message": "Hi",
        **extra,
    }


# Create and attach


def test_a_new_buyer_makes_one_lead_with_one_created_event(pipe: Pipeline) -> None:
    message = pipe.deliver("inbox", portal())
    assert message.status == "lead_created"
    assert message.reason == "new lead"
    assert message.needs_reread is True
    lead = pipe.lead(message.lead_id)
    assert (lead.name, lead.phone, lead.email, lead.item) == (
        "Ana Ruiz",
        "+12025550142",
        "ana.ruiz@inbox.example",
        "Cedar Point Lofts",
    )
    assert pipe.team_slug(lead) == "north"
    assert lead.state == "pool"
    events = pipe.events(lead.id)
    assert [(e.type, e.seq) for e in events] == [("lead.created", 1)]
    assert events[0].cause == f"message:{message.id}"
    assert events[0].id.version == 7


def test_a_later_message_from_the_buyer_attaches_with_the_next_event(pipe: Pipeline) -> None:
    first = pipe.deliver("inbox", portal())
    second = pipe.deliver(
        "inbox", email("Ana Ruiz <Ana.Ruiz@Inbox.example>", "Any news on Cedar Point Lofts?")
    )
    assert second.status == "attached"
    assert second.lead_id == first.lead_id
    assert pipe.count(Lead) == 1
    assert pipe.count(Activity) == 1
    assert first.lead_id is not None
    events = pipe.events(first.lead_id)
    assert [(e.type, e.seq) for e in events] == [("lead.created", 1), ("lead.activity", 2)]


def test_processing_a_message_twice_changes_nothing(pipe: Pipeline) -> None:
    message = pipe.deliver("inbox", portal())
    with pipe.sessions.begin() as session:
        session.execute(update(Job).values(state="queued"))
    pipe.run()
    assert pipe.message(message.id).status == "lead_created"
    assert pipe.count(Lead) == 1
    assert pipe.count(LeadEvent) == 1


def test_the_same_form_body_after_ten_minutes_attaches_as_a_new_message(pipe: Pipeline) -> None:
    payload = site("Ana Ruiz", "202-555-0142", "ana@inbox.example", "Juniper Mesa")
    first = pipe.deliver_form("site-form", payload)
    with pipe.sessions.begin() as session:
        session.execute(
            update(Message).values(received_at=func.clock_timestamp() - timedelta(minutes=11))
        )
    second = pipe.deliver_form("site-form", payload)
    assert second.id != first.id
    assert second.status == "attached"
    assert second.lead_id == first.lead_id


def test_a_re_forward_with_a_new_message_id_is_duplicate_content(pipe: Pipeline) -> None:
    original = pipe.deliver("inbox", portal())
    forward = (
        "FYI\n\n---------- Forwarded message ---------\n"
        "From: Hearthly <leads@hearthly.example>\nDate: Mon, Mar 2, 2026 at 8:10 AM\n"
        "Subject: Hearthly: new inquiry\nTo: <leads@larkspur.example>\n\n" + PORTAL
    )
    again = pipe.deliver("inbox", email("Sam Whitley <sam@larkspur.example>", forward, "Fwd: lead"))
    assert again.status == "duplicate_content"
    assert again.reason
    assert again.lead_id == original.lead_id
    assert pipe.count(Activity) == 0
    assert pipe.count(LeadEvent) == 1


def test_a_re_forward_after_24_hours_attaches(pipe: Pipeline) -> None:
    original = pipe.deliver("inbox", portal())
    with pipe.sessions.begin() as session:
        session.execute(
            update(Message).values(received_at=func.clock_timestamp() - timedelta(hours=25))
        )
    again = pipe.deliver("inbox", portal(message_id="<another@mail.example>"))
    assert again.status == "attached"
    assert again.lead_id == original.lead_id


def test_an_unparseable_message_fails_with_a_reason(pipe: Pipeline) -> None:
    broken = pipe.deliver("site-form", b"{not json")
    assert broken.status == "failed"
    assert broken.reason == "unparseable: not JSON"
    array = pipe.deliver("site-form", b"[1, 2]")
    assert array.reason == "unparseable: JSON is not an object"
    empty = pipe.deliver("inbox", b"Subject: nothing\n\n")
    assert empty.status == "failed"
    assert pipe.count(Lead) == 0


# F7: arrival time and source time


@pytest.mark.parametrize(
    "date",
    ["Sat, 01 Jan 2099 09:00:00 +0000", "Thu, 01 Jan 1970 00:00:01 +0000", "not a date", None],
)
def test_arrival_comes_from_the_database_clock_whatever_the_date_header_says(
    pipe: Pipeline, date: str | None
) -> None:
    before = datetime.now(UTC)
    message = pipe.deliver("inbox", portal(date=date) if date else portal(date=""))
    after = datetime.now(UTC)
    lead = pipe.lead(message.lead_id)
    assert lead.arrival_at == message.received_at
    assert before - timedelta(seconds=5) <= message.received_at <= after + timedelta(seconds=5)
    if date and date.startswith("Sat"):
        assert message.source_time == datetime(2099, 1, 1, 9, tzinfo=UTC)
    elif date and date.startswith("Thu"):
        assert message.source_time == datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)
    else:
        assert message.source_time is None


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Date: Mon, Mar 2, 2026 at 8:10 AM", datetime(2026, 3, 2, 13, 10, tzinfo=UTC)),
        ("Sent: Monday, March 2, 2026 11:45 PM", datetime(2026, 3, 3, 4, 45, tzinfo=UTC)),
    ],
)
def test_a_forwarded_portal_time_becomes_the_source_time(
    pipe: Pipeline, header: str, expected: datetime
) -> None:
    marker = (
        "---------- Forwarded message ---------"
        if header.startswith("Date")
        else ("-----Original Message-----")
    )
    body = f"{marker}\nFrom: Hearthly <leads@hearthly.example>\n{header}\nSubject: x\n\n{PORTAL}"
    message = pipe.deliver(
        "inbox",
        email("Sam <sam@larkspur.example>", body, "Fwd: x", date="Tue, 03 Mar 2026 10:00:00 -0500"),
    )
    assert message.source_time == expected
    assert message.status == "lead_created"
    lead = pipe.lead(message.lead_id)
    assert lead.email == "ana.ruiz@inbox.example"
    assert lead.arrival_at == message.received_at


# F9: matching rules, each hard-negative kind, the conflict flag


def test_same_email_in_the_placed_team_attaches(pipe: Pipeline) -> None:
    first = pipe.deliver_form(
        "site-form", site("Ana Ruiz", mail="ana@inbox.example", listing="Juniper Mesa")
    )
    second = pipe.deliver_form(
        "site-form", site("A. Ruiz", mail="ANA@inbox.example", listing="Copperleaf")
    )
    assert second.status == "attached"
    assert second.lead_id == first.lead_id


def test_same_phone_and_same_first_name_attaches(pipe: Pipeline) -> None:
    first = pipe.deliver_form("site-form", site("Ana Ruiz", "202-555-0142", listing="Juniper Mesa"))
    second = pipe.deliver_form("site-form", site("Ana R", "(202) 555-0142", listing="Copperleaf"))
    assert second.lead_id == first.lead_id


def test_same_phone_and_a_blank_name_attaches(pipe: Pipeline) -> None:
    first = pipe.deliver_form("site-form", site("Ana Ruiz", "202-555-0142", listing="Juniper Mesa"))
    second = pipe.deliver_form(
        "calls", {"caller": {"name": "", "number": "+12025550142"}, "listing": "Copperleaf"}
    )
    assert second.status == "attached"
    assert second.lead_id == first.lead_id


def test_household_phone_with_a_different_first_name_is_a_flagged_new_lead(pipe: Pipeline) -> None:
    first = pipe.deliver_form("site-form", site("Ana Ruiz", "202-555-0142", listing="Juniper Mesa"))
    second = pipe.deliver_form("site-form", site("Luis Ruiz", "202-555-0142", listing="Copperleaf"))
    assert second.status == "lead_created"
    assert second.reason == "new lead, possible duplicate"
    assert pipe.lead(second.lead_id).possible_duplicates == [str(first.lead_id)]


def test_phone_to_one_lead_and_email_to_another_is_a_flagged_new_lead(pipe: Pipeline) -> None:
    a = pipe.deliver_form(
        "site-form", site("Ana Ruiz", "202-555-0142", "ana@inbox.example", "Juniper Mesa")
    )
    b = pipe.deliver_form(
        "site-form", site("Bea Cole", "303-555-0177", "bea@inbox.example", "Juniper Mesa")
    )
    c = pipe.deliver_form(
        "site-form", site("Ana Ruiz", "202-555-0142", "bea@inbox.example", "Juniper Mesa")
    )
    assert c.status == "lead_created"
    assert pipe.lead(c.lead_id).possible_duplicates == sorted([str(a.lead_id), str(b.lead_id)])
    assert pipe.count(Lead) == 3


def test_with_no_team_one_hit_attaches_to_that_lead_and_its_team(pipe: Pipeline) -> None:
    first = pipe.deliver_form(
        "site-form", site("Ana Ruiz", mail="ana@inbox.example", listing="Juniper Mesa")
    )
    second = pipe.deliver("inbox", email("Ana Ruiz <ana@inbox.example>", "Following up!"))
    assert second.status == "attached"
    assert second.lead_id == first.lead_id
    assert second.team_id == pipe.lead(first.lead_id).team_id


def test_with_no_team_two_hits_go_to_unassigned_flagged(pipe: Pipeline) -> None:
    a = pipe.deliver_form(
        "site-form", site("Ana Ruiz", mail="ana@inbox.example", listing="Juniper Mesa")
    )
    b = pipe.deliver_form(
        "site-form", site("Ana Ruiz", mail="ana@inbox.example", listing="Cedar Point Lofts")
    )
    assert b.status == "lead_created"
    c = pipe.deliver("inbox", email("Ana Ruiz <ana@inbox.example>", "Following up!"))
    assert c.status == "lead_created"
    lead = pipe.lead(c.lead_id)
    assert lead.team_id is None
    assert lead.possible_duplicates == sorted([str(a.lead_id), str(b.lead_id)])


def test_a_hit_only_in_another_team_makes_a_new_lead_in_the_placed_team(pipe: Pipeline) -> None:
    pipe.deliver_form(
        "site-form", site("Ana Ruiz", mail="ana@inbox.example", listing="Juniper Mesa")
    )
    other = pipe.deliver_form(
        "site-form", site("Ana Ruiz", mail="ana@inbox.example", listing="Cedar Point Lofts")
    )
    assert other.status == "lead_created"
    assert pipe.team_slug(pipe.lead(other.lead_id)) == "north"
    assert pipe.lead(other.lead_id).possible_duplicates == []


def _agent_email(client: str, item: str) -> bytes:
    body = (
        f"Hello,\n\nI'm writing on behalf of my client, {client}, who would like to see {item}.\n"
        "Please contact me directly to set up a showing.\n\nBest regards,\nDana Whitcomb\n"
        "(202) 555-0101\ndana@keyhavenagents.example"
    )
    return email("Dana Whitcomb <dana@keyhavenagents.example>", body, f"Showing request: {item}")


def test_hard_negative_shared_agent_phone_never_merges(pipe: Pipeline) -> None:
    first = pipe.deliver("inbox", _agent_email("Ana Ruiz", "Juniper Mesa"))
    second = pipe.deliver("inbox", _agent_email("Luis Ortiz", "Juniper Mesa"))
    assert first.status == second.status == "lead_created"
    assert first.lead_id != second.lead_id
    assert pipe.count(Identity) == 0
    lead = pipe.lead(second.lead_id)
    assert (lead.phone, lead.email, lead.possible_duplicates) == (None, None, [])


def test_hard_negative_portal_relay_sender_never_merges(pipe: Pipeline) -> None:
    other = (
        PORTAL.replace("Ana Ruiz", "Bea Cole")
        .replace("(202) 555-0142", "")
        .replace("ana.ruiz@inbox.example", "")
    )
    first = pipe.deliver("inbox", portal())
    second = pipe.deliver("inbox", portal(other))
    assert second.status == "lead_created"
    assert second.lead_id != first.lead_id
    assert pipe.lead(second.lead_id).email is None


def test_hard_negative_call_tracking_number_never_merges(pipe: Pipeline) -> None:
    def call(name: str, number: str) -> dict[str, object]:
        return {
            "caller": {"name": name, "number": number},
            "tracking_number": "+18005550120",
            "listing": "Juniper Mesa",
            "transcript": "Caller: call me back.",
        }

    first = pipe.deliver_form("calls", call("Ana Ruiz", "+12025550142"))
    second = pipe.deliver_form("calls", call("Bea Cole", "+18005550120"))
    assert second.status == "lead_created"
    assert second.lead_id != first.lead_id
    assert pipe.lead(second.lead_id).phone is None


def test_a_known_buyer_message_with_spam_signals_still_attaches(pipe: Pipeline) -> None:
    first = pipe.deliver_form(
        "site-form", site("Ana Ruiz", mail="ana@inbox.example", listing="Juniper Mesa")
    )
    spammy = pipe.deliver_form(
        "site-form", site("Ana Ruiz", mail="ana@inbox.example", listing="Juniper Mesa", website="x")
    )
    assert spammy.category == "spam"
    assert spammy.status == "attached"
    assert spammy.lead_id == first.lead_id


# F10: every source goes through the category step


@pytest.mark.parametrize("honeypot", ["https://growth-hacks.example/offer", ""])
def test_a_spam_form_post_makes_no_lead(pipe: Pipeline, honeypot: str) -> None:
    sender = "seo@growth-hacks.example" if honeypot else "seo@seo-rocket.example"
    message = pipe.deliver_form("site-form", site("SEO Expert", mail=sender, website=honeypot))
    assert message.status == "not_a_lead"
    assert message.reason == ("spam (honeypot)" if honeypot else "spam (spam_sender)")
    assert message.lead_id is None
    assert pipe.count(Lead) == 0


def test_a_spam_email_makes_no_lead(pipe: Pipeline) -> None:
    message = pipe.deliver("inbox", email("Offers <win@prize-center.example>", "You won!"))
    assert message.status == "not_a_lead"
    assert pipe.count(Lead) == 0


def test_a_spam_partner_form_makes_no_lead(pipe: Pipeline) -> None:
    payload = {"contact": {"first": "Best", "last": "Offers", "email": "deals@cheap-meds.example"}}
    message = pipe.deliver_form("partner-form", payload, {"X-Delivery-Id": "dlv-9"})
    assert message.status == "not_a_lead"
    assert pipe.count(Lead) == 0


def test_every_processed_message_ends_terminal_with_a_reason(pipe: Pipeline) -> None:
    pipe.deliver("inbox", portal())
    pipe.deliver_form("site-form", site("SEO", mail="a@seo-rocket.example"))
    pipe.deliver("site-form", b"nope")
    with pipe.sessions() as session:
        rows = session.query(Message.status, Message.reason).all()
    assert {status for status, _ in rows} == {"lead_created", "not_a_lead", "failed"}
    assert all(reason for _, reason in rows)
