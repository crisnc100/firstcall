"""Alerts and escalation: who is told, push at once with a team tag, one digest email per
burst, push pruning and retries, safe email (F24, F26), and the recipient allowlist (F27)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import alerts
from firstcall.board.escalate import escalate
from firstcall.db import Alert, Lead, PushSend, PushSubscription, User
from firstcall.outbound import allowlist
from firstcall.outbound.mail import Line, SmtpMailer, label, render
from firstcall.pipeline.worker import alert_tick, work
from firstcall.settings import Settings
from tests.board_helpers import Board, FakePushService, Mailpit, vapid_keys
from tests.conftest import FakeClock

MONDAY_10AM_NEW_YORK = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    return Board(sessions, clock)


@pytest.fixture
def push() -> Iterator[FakePushService]:
    service = FakePushService()
    yield service
    service.close()


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _alerts(board: Board) -> set[tuple[uuid.UUID, str, uuid.UUID]]:
    with board.sessions() as session:
        return {(a.lead_id, a.kind, a.user_id) for a in session.scalars(select(Alert))}


def _smtp() -> SmtpMailer:
    settings = Settings()
    return SmtpMailer(settings.smtp_host, settings.smtp_port, settings.email_from)


# --- who is told (F26) ------------------------------------------------------------------


def test_a_new_lead_alerts_its_pool_and_nobody_outside_it(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
    board.user("south@larkspur.example", teams={"south": "salesperson"})
    monitor = board.user("monitor@larkspur.example", roles=["monitor"])
    board.user("admin@larkspur.example", roles=["admin"])
    gone = board.user("gone@larkspur.example", teams={"north": "salesperson"})
    with board.sessions.begin() as session:
        session.get_one(User, gone.id).active = False
    north, unassigned = board.lead("north"), board.lead(None)
    assert _alerts(board) == {
        (north, "new_lead", seller.id),
        (north, "new_lead", lead_user.id),
        # The monitor is told the moment a lead lands in Unassigned.
        (unassigned, "new_lead", monitor.id),
    }


def test_activity_alerts_only_the_owner_and_a_pool_lead_gets_nothing_fresh(board: Board) -> None:
    owner = board.user("owner@larkspur.example", teams={"north": "salesperson"})
    board.user("mate@larkspur.example", teams={"north": "salesperson"})
    owned = board.lead("north", name="Ana Ruiz", email="ana@buyers.example")
    pooled = board.lead("north", name="Bo Lin", email="bo@buyers.example")
    board.own(owned, owner)
    before = _alerts(board)
    for name, address in (("Ana Ruiz", "ana@buyers.example"), ("Bo Lin", "bo@buyers.example")):
        message = board.pipe.deliver_form(
            "site-form",
            {"name": name, "email": address, "listing": "Cedar Point Lofts", "message": "Hi again"},
        )
        assert message.status == "attached"
    assert _alerts(board) - before == {(owned, "activity", owner.id)}
    assert all(lead != pooled for lead, _, _ in _alerts(board) - before)


# --- escalation (F24) -------------------------------------------------------------------


def test_each_escalation_fires_once_at_its_time_and_not_before(board: Board) -> None:
    lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
    board.user("seller@larkspur.example", teams={"north": "salesperson"})
    admin = board.user("admin@larkspur.example", roles=["admin"])
    monitor = board.user("monitor@larkspur.example", roles=["monitor"])
    north, unassigned, claimed = board.lead("north"), board.lead(None), board.lead("north")
    for lead in (north, unassigned, claimed):
        board.set_lead(lead, arrival_at=MONDAY_10AM_NEW_YORK, pool_since=MONDAY_10AM_NEW_YORK)
    board.own(claimed, lead_user)
    start = _alerts(board)

    def fired_at(minutes: float) -> set[tuple[uuid.UUID, str, uuid.UUID]]:
        board.clock.current = MONDAY_10AM_NEW_YORK + timedelta(minutes=minutes)
        before = _alerts(board)
        escalate(board.sessions, board.clock)
        return _alerts(board) - before

    assert fired_at(14.9) == set()
    assert fired_at(15) == {
        (north, "escalation:team_unclaimed", lead_user.id),
        (unassigned, "escalation:unassigned_still", admin.id),
    }
    assert fired_at(15) == set()
    assert fired_at(59) == set()
    assert fired_at(60) == {
        (north, "escalation:team_still_unclaimed", admin.id),
        (north, "escalation:team_still_unclaimed", monitor.id),
    }
    assert fired_at(600) == set()
    assert all(lead != claimed for lead, _, _ in _alerts(board) - start)


def test_a_team_level_waits_for_business_hours(board: Board) -> None:
    lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
    lead = board.lead("north")
    entered = datetime(2026, 3, 2, 22, 55, tzinfo=UTC)  # 17:55 local
    board.set_lead(lead, arrival_at=entered, pool_since=entered)
    board.clock.current = datetime(2026, 3, 3, 14, 9, tzinfo=UTC)  # Tue 09:09: 14 minutes
    assert escalate(board.sessions, board.clock) == 0
    board.clock.advance(minutes=1)
    assert escalate(board.sessions, board.clock) == 1
    assert (lead, "escalation:team_unclaimed", lead_user.id) in _alerts(board)


# --- push and email against the fake push service and Mailpit (F26) --------------------


def test_push_goes_out_at_once_with_the_team_tag_and_email_waits_for_one_digest(
    board: Board, push: FakePushService
) -> None:
    run = _tag()
    seller = board.user(f"seller-{run}@larkspur.example", teams={"north": "salesperson"})
    device = push.device()
    board.subscribe(seller, device)
    private, _ = vapid_keys()
    outbound = board.outbound(private, _smtp())
    for n in range(3):
        board.pipe.form(
            "site-form",
            {
                "name": f"Buyer{n} Test",
                "email": f"b{n}-{run}@buyers.example",
                "listing": "Alder Ridge",
            },
        )
    work(board.sessions, board.clock, board.settings, until_idle=True, outbound=outbound)

    received = push.to(device)
    assert len(received) == 3
    payloads = [device.read(r.body) for r in received]
    assert {p["tag"] for p in payloads} == {"team-north"}
    assert {p["title"] for p in payloads} == {"New lead in North"}
    assert all(r.headers["authorization"].startswith("vapid t=") for r in received)

    mailpit = Mailpit()
    assert mailpit.to(seller.email) == []
    board.clock.advance(seconds=59)
    assert outbound.email_digests() == 0
    board.clock.advance(seconds=1)
    assert alert_tick(board.sessions, board.clock, outbound)["emailed"] == 1
    assert alert_tick(board.sessions, board.clock, outbound)["emailed"] == 0
    mails = mailpit.to(seller.email)
    assert [m["Subject"] for m in mails] == ["3 new leads in North"]
    body = mailpit.message(mails[0]["ID"])
    assert body["Text"].count("New lead: Buyer") == 3
    assert [a["Address"] for a in body["To"]] == [seller.email]


def test_a_gone_device_is_pruned_and_a_5xx_is_retried_three_times(
    board: Board, push: FakePushService
) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    devices = {name: push.device() for name in ("gone", "missing", "flaky", "down")}
    for device in devices.values():
        board.subscribe(seller, device)
    push.answers[devices["gone"].endpoint.split(f":{push.port}")[1]] = [410]
    push.answers[devices["missing"].endpoint.split(f":{push.port}")[1]] = [404]
    push.answers[devices["flaky"].endpoint.split(f":{push.port}")[1]] = [503, 201]
    push.answers[devices["down"].endpoint.split(f":{push.port}")[1]] = [500, 502, 503, 504, 201]
    private, _ = vapid_keys()
    outbound = board.outbound(private)
    board.lead("north")
    for _ in range(6):
        outbound.push_pending()

    with board.sessions() as session:
        left = set(session.scalars(select(PushSubscription.endpoint)))
        sends = {s.last_status: (s.state, s.attempts) for s in session.scalars(select(PushSend))}
    assert left == {devices["flaky"].endpoint, devices["down"].endpoint}
    assert sends[410] == ("gone", 1)
    assert sends[404] == ("gone", 1)
    assert sends[201] == ("sent", 2)
    assert sends[504] == ("failed", 4)
    assert len(push.to(devices["down"])) == 4
    assert len(push.to(devices["flaky"])) == 2


def test_buyer_text_is_escaped_in_the_email_and_kept_out_of_the_headers(board: Board) -> None:
    run = _tag()
    seller = board.user(f"seller-{run}@larkspur.example", teams={"north": "salesperson"})
    lead = board.lead("north")
    hostile = "<script>alert(1)</script>\r\nBcc: victim@evil.example\r\n\r\n<b>x</b>"
    board.set_lead(lead, name=hostile, item="Cedar <i>Point</i>")
    board.clock.advance(seconds=60)
    assert board.outbound(mailer=_smtp()).email_digests() == 1

    mailpit = Mailpit()
    [mail] = mailpit.to(seller.email)
    body = mailpit.message(mail["ID"])
    assert "<script>" not in body["HTML"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body["HTML"]
    assert "&lt;i&gt;Point&lt;/i&gt;" in body["HTML"]
    assert body["Bcc"] == []
    assert [a["Address"] for a in body["To"]] == [seller.email]
    headers = mailpit.raw(mail["ID"]).split("\r\n\r\n", 1)[0]
    assert not re.search(r"(?im)^bcc:", headers)
    assert "evil.example" not in headers
    assert "\r" not in mail["Subject"]
    assert "\n" not in mail["Subject"]
    assert mail["Subject"].startswith("New lead in North: <script>alert(1)</script>")


# --- the allowlist (F27) ----------------------------------------------------------------


def test_email_to_a_buyer_is_refused_even_through_a_crafted_user(board: Board) -> None:
    run = _tag()
    buyer_email = f"buyer-{run}@buyers.example"
    board.lead("north", email=buyer_email, phone="202-555-0187")
    crafted = board.user(buyer_email, teams={"north": "salesperson"})
    texting = board.user("2025550187@txt.example", teams={"north": "salesperson"})
    board.lead("north")
    assert board.org_id is not None
    with board.sessions() as session:
        for address in (buyer_email, "2025550187@txt.example", "stranger@else.example"):
            with pytest.raises(allowlist.RefusedError):
                allowlist.check_email(session, board.org_id, address)
        with pytest.raises(allowlist.RefusedError):
            allowlist.check_email(session, board.org_id, "x\r\nbcc: y@z.example")
    board.clock.advance(seconds=60)
    assert board.outbound(mailer=_smtp()).email_digests() == 0
    with board.sessions() as session:
        states = {
            (a.user_id, a.email_state)
            for a in session.scalars(select(Alert))
            if a.user_id in (crafted.id, texting.id)
        }
    assert states == {(crafted.id, "refused"), (texting.id, "refused")}
    assert Mailpit().to(buyer_email) == []


def test_push_only_to_the_recipients_own_https_device(board: Board, push: FakePushService) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    other = board.user("other@larkspur.example", teams={"south": "salesperson"})
    board.subscribe(other, push.device())
    plain = push.device()
    board.subscribe(seller, plain)
    with board.sessions() as session:
        theirs = session.scalars(
            select(PushSubscription).where(PushSubscription.user_id == other.id)
        ).one()
        mine = session.scalars(
            select(PushSubscription).where(PushSubscription.user_id == seller.id)
        ).one()
        hosts = Settings(env="test").push_hosts
        with pytest.raises(allowlist.RefusedError, match="does not belong"):
            allowlist.check_push(session, theirs, seller.id, allow_http=True, hosts=hosts)
        with pytest.raises(allowlist.RefusedError, match="push service"):
            allowlist.check_push(session, mine, seller.id, allow_http=False, hosts=hosts)
        allowlist.check_push(session, mine, seller.id, allow_http=True, hosts=hosts)


# --- edges of queueing and rendering ----------------------------------------------------


def test_queueing_the_same_cause_twice_adds_nothing_and_a_gone_owner_gets_no_activity(
    board: Board,
) -> None:
    owner = board.user("owner@larkspur.example", teams={"north": "salesperson"})
    lead_id = board.lead("north")
    board.own(lead_id, owner)
    with board.sessions.begin() as session:
        lead = session.get_one(Lead, lead_id)
        assert alerts.queue(session, lead, "assigned", "test:1", [owner], board.clock.now()) == 1
        assert alerts.queue(session, lead, "assigned", "test:1", [owner], board.clock.now()) == 0
        session.get_one(User, owner.id).active = False
        session.flush()
        assert alerts.activity(session, lead, "test:2", board.clock.now()) == 0


def test_the_digest_subject_for_mixed_alerts_and_an_escalation_label() -> None:
    lines = [
        Line("new_lead", "Ana Ruiz", None, "https://x.example/leads/1"),
        Line("escalation:team_unclaimed", "Bo Lin", "Alder Ridge", "https://x.example/leads/2"),
    ]
    mail = render("North", lines)
    assert mail.subject == "2 alerts in North"
    assert "Still unclaimed: Bo Lin (Alder Ridge)" in mail.text
    assert label("something_new") == "Alert"
