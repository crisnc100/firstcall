"""PR2 review fixes in the pages, in Chromium on compose.

- an inbox digest links only server paths: a buyer name holding `//attacker.example`
  never becomes a link;
- a sign-out the server never confirmed keeps the app covered and retries, and shows
  the sign-in page only once the session is really gone;
- "Claimed … ago" counts from the current owner's claim and moves on with the timers;
- on a phone, "More actions" opens and closes the extra actions;
- Enter in "New password" sets the password instead of saving the edit form.
"""

from __future__ import annotations

import re
import subprocess
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from playwright.sync_api import Route, expect, sync_playwright
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.db import InboxEmail, Lead, Message, Team, User
from firstcall.seed import seed
from tests.conftest import FakeClock
from tests.fault.test_pages import PASSWORD, _free_port, _store, _wait_for
from tests.fault.test_pages import compose_db as compose_db  # a fixture, found by name
from tests.fault.test_pages_qa import ALL_INTERFACES, _sign_in, _start

NEW_PASSWORD = "a brand new password"  # pragma: allowlist secret
SIGNIN = re.compile(r"/signin$")


def test_the_review_fixes_in_a_browser(compose_db: tuple[sessionmaker[Session], str]) -> None:
    sessions, container_url = compose_db
    seed(sessions)
    with sessions.begin() as session:
        north = session.scalars(select(Team).where(Team.slug == "north")).one()
        people = {}
        for email, name, roles, team_role in (
            ("one@larkspur.example", "One Seller", [], "salesperson"),
            ("tess@larkspur.example", "Tess Lead", [], "team_lead"),
            ("ada@larkspur.example", "Ada Admin", ["admin"], None),
        ):
            user = accounts.create_user(session, north.org_id, email, name, roles, PASSWORD)
            if team_role:
                accounts.set_memberships(session, user, {north.id: team_role}, FakeClock())
            people[email] = user.id

    run = uuid.uuid4().hex[:8]
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    hostile = f"{base}//attacker.example/signin"
    lure = _store(sessions, {"name": hostile, "listing": "Alder Ridge", "phone": "202-555-0181"})
    owned = _store(sessions, {"name": "Olive Owned", "listing": "Cedar Point Lofts"})

    names = [f"firstcall-review-worker-{run}", f"firstcall-review-api-{run}"]
    _start(
        names[0],
        container_url,
        "worker",
        "worker",
        FIRSTCALL_DIGEST_SECONDS="1",
        FIRSTCALL_BASE_URL=base,
    )
    _start(
        names[1],
        container_url,
        "api",
        "api",
        "--host",
        ALL_INTERFACES,
        "--port",
        "8000",
        port=str(port),
    )
    try:

        def processed() -> bool:
            with sessions() as session:
                rows = session.scalars(
                    select(Message.status).where(Message.id.in_([lure, owned]))
                ).all()
                return len(rows) == 2 and all(s == "lead_created" for s in rows)

        def api_up() -> bool:
            try:
                return httpx.get(f"{base}/healthz", timeout=2).status_code == 200
            except httpx.HTTPError:
                return False

        def lure_mailed() -> bool:
            with sessions() as session:
                found = session.scalars(
                    select(InboxEmail.subject).where(
                        InboxEmail.user_id == people["one@larkspur.example"],
                        InboxEmail.text_body.contains("attacker.example"),
                    )
                ).first()
                subject.extend([found] if found else [])
                return found is not None

        subject: list[str] = []
        _wait_for(processed, "the worker to process the messages")
        _wait_for(api_up, "the api container")
        _wait_for(lure_mailed, "the digest naming the hostile buyer")
        five_ago = datetime.now(UTC) - timedelta(minutes=5)
        long_ago = datetime.now(UTC) - timedelta(days=2)
        with sessions.begin() as session:
            lead_id = session.get_one(Message, owned).lead_id
            # First claimed two days ago by someone else; the seller has had it 5 minutes.
            session.execute(
                update(Lead)
                .where(Lead.id == lead_id)
                .values(
                    state="owned",
                    owner_id=people["one@larkspur.example"],
                    pool_since=None,
                    claimed_at=long_ago,
                    owned_since=five_ago,
                )
            )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()

            one = browser.new_context(base_url=base).new_page()
            one.clock.install(time=datetime.now(UTC))
            _sign_in(one, "one@larkspur.example")
            expect(one).not_to_have_url(SIGNIN)
            one.goto("/mine")
            row = one.locator("li.row", has=one.get_by_role("link", name="Olive Owned"))
            expect(row.get_by_text("Claimed 5 min ago")).to_be_visible()
            one.clock.fast_forward("02:00")
            expect(row.get_by_text("Claimed 7 min ago")).to_be_visible()

            one.goto("/inbox")
            one.get_by_role("button", name=subject[0]).first.click()
            expect(one.get_by_text("attacker.example").first).to_be_visible()
            hrefs = one.locator("main a").evaluate_all(
                "(links) => links.map((a) => a.getAttribute('href'))"
            )
            assert hrefs, "the digest should link its leads"
            for href in hrefs:
                assert href.startswith("/"), href
                assert not href.startswith("//"), href
                assert "attacker" not in href, href
            lure_lead = one.get_by_role("link", name=f"Open {hostile}")
            expect(lure_lead).to_have_attribute("href", re.compile(r"^/leads/[0-9a-f-]{36}$"))

            def refuse(route: Route) -> None:
                if route.request.method == "DELETE":
                    route.abort()
                else:
                    route.continue_()

            one.route("**/api/session", refuse)
            one.get_by_role("button", name="One Seller").click()
            one.get_by_role("button", name="Sign out").click()
            expect(one.get_by_role("heading", name="Not signed out yet")).to_be_visible()
            assert not one.url.endswith("/signin")
            one.unroute("**/api/session")
            one.get_by_role("button", name="Try again now").click()
            expect(one).to_have_url(SIGNIN)
            one.goto("/mine")
            expect(one).to_have_url(SIGNIN)

            tess = browser.new_context(
                base_url=base, viewport={"width": 390, "height": 844}
            ).new_page()
            _sign_in(tess, "tess@larkspur.example")
            expect(tess).not_to_have_url(SIGNIN)
            tess.goto(f"/leads/{lead_id}")
            reassign = tess.get_by_role("button", name=re.compile("^Reassign"))
            more = tess.get_by_role("button", name="More actions")
            expect(more).to_be_visible()
            expect(reassign).to_be_hidden()
            more.click()
            expect(reassign).to_be_visible()
            more.click()
            expect(reassign).to_be_hidden()

            ada = browser.new_context(base_url=base).new_page()
            _sign_in(ada, "ada@larkspur.example")
            expect(ada).not_to_have_url(SIGNIN)
            ada.goto("/admin/users")
            ada.get_by_role("button", name="Edit One Seller").first.click()
            ada.get_by_role("button", name="Set a new password").click()
            ada.get_by_label("New password").fill(NEW_PASSWORD)
            ada.get_by_label("New password").press("Enter")
            expect(ada.get_by_text("Password set.").first).to_be_visible()
            browser.close()

        with sessions() as session:
            seller = session.get_one(User, people["one@larkspur.example"])
            assert accounts.verify(session, seller.email, NEW_PASSWORD) is not None
    finally:
        for container in names:
            subprocess.run(["docker", "stop", container], capture_output=True, timeout=60)
