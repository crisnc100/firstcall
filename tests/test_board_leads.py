"""Lead actions on the board: the access matrix (F20), one event per transition (F22), time to
claim (F23), owner left (F25), and the pool and team lists."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.db import Alert, LeadEvent, User
from tests.board_helpers import Board
from tests.conftest import FakeClock

ROUTES = ("detail", "claim", "release", "return", "reassign", "move", "place")
ROLES = ("salesperson", "team_lead", "monitor", "admin")
WHERE = ("own", "other", "unassigned")
TEAM = {"own": "north", "other": "south", "unassigned": None}

# Everything not listed answers 404. 409 means the role may take the action, on a lead it
# can see, but the lead is not where or in the state the action needs.
ALLOWED: dict[tuple[str, str, str], int] = {
    **{("salesperson", r, "own"): 200 for r in ("detail", "claim", "release")},
    **{
        ("team_lead", r, "own"): 200
        for r in ("detail", "claim", "release", "return", "reassign", "move")
    },
    **{("monitor", "detail", w): 200 for w in WHERE},
    ("monitor", "place", "own"): 409,
    ("monitor", "place", "other"): 409,
    ("monitor", "place", "unassigned"): 200,
    **{("admin", "detail", w): 200 for w in WHERE},
    **{("admin", r, w): 200 for r in ("return", "reassign", "move") for w in ("own", "other")},
    ("admin", "place", "own"): 409,
    ("admin", "place", "other"): 409,
    ("admin", "place", "unassigned"): 200,
    **{("admin", r, "unassigned"): 409 for r in ("return", "reassign", "move")},
}


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    return Board(sessions, clock)


class Cast:
    def __init__(self, board: Board) -> None:
        self.actors = {
            "salesperson": board.user("seller@larkspur.example", teams={"north": "salesperson"}),
            "team_lead": board.user("lead@larkspur.example", teams={"north": "team_lead"}),
            "monitor": board.user("monitor@larkspur.example", roles=["monitor"]),
            "admin": board.user("admin@larkspur.example", roles=["admin"]),
        }
        self.owner = {
            "north": board.user("north2@larkspur.example", teams={"north": "salesperson"}),
            "south": board.user("south1@larkspur.example", teams={"south": "salesperson"}),
        }
        self.target = {
            "north": board.user("north3@larkspur.example", teams={"north": "salesperson"}),
            "south": board.user("south2@larkspur.example", teams={"south": "salesperson"}),
        }
        self.clients = {role: board.signed_in(user.email) for role, user in self.actors.items()}


def _setup(board: Board, cast: Cast, role: str, route: str, where: str) -> uuid.UUID:
    team = TEAM[where]
    lead = board.lead(team)
    if team is None:
        return lead
    actor = cast.actors[role]
    if route == "release":
        member = role in ("salesperson", "team_lead") and team == "north"
        board.own(lead, actor if member else cast.owner[team])
    elif route in ("return", "reassign"):
        board.own(lead, cast.owner[team])
    return lead


def _call(board: Board, cast: Cast, role: str, route: str, where: str, lead: uuid.UUID) -> Any:
    client = cast.clients[role]
    team = TEAM[where] or "north"
    if route == "detail":
        return client.get(f"/api/leads/{lead}")
    if route == "reassign":
        return client.post(
            f"/api/leads/{lead}/reassign", json={"user_id": str(cast.target[team].id)}
        )
    if route == "move":
        return client.post(f"/api/leads/{lead}/move", json={"team_id": str(board.teams["east"])})
    if route == "place":
        return client.post(f"/api/leads/{lead}/place", json={"team_id": str(board.teams["north"])})
    return client.post(f"/api/leads/{lead}/{route}")


def _events(board: Board, lead: uuid.UUID) -> list[LeadEvent]:
    return board.pipe.events(lead)


def _event_ids(board: Board, lead: uuid.UUID) -> list[uuid.UUID]:
    return [e.id for e in board.pipe.events(lead)]


def test_the_access_matrix(board: Board) -> None:
    cast = Cast(board)
    missing = cast.clients["admin"].get(f"/api/leads/{uuid.uuid4()}")
    assert missing.status_code == 404
    results: dict[tuple[str, str, str], int] = {}
    for role in ROLES:
        for route in ROUTES:
            for where in WHERE:
                lead = _setup(board, cast, role, route, where)
                before = _event_ids(board, lead)
                state_before = board.get(lead).state
                answer = _call(board, cast, role, route, where, lead)
                results[(role, route, where)] = answer.status_code
                if answer.status_code == 404:
                    # Exactly the answer a lead that does not exist gets: nothing leaks.
                    assert answer.json() == missing.json(), (role, route, where)
                if answer.status_code != 200 or route == "detail":
                    assert _event_ids(board, lead) == before, (role, route, where)
                    assert board.get(lead).state == state_before
                else:
                    assert _event_ids(board, lead)[:-1] == before
    expected = {key: ALLOWED.get(key, 404) for key in results}
    assert results == expected


def test_team_a_never_sees_team_b_in_any_list(board: Board) -> None:
    board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("lead@larkspur.example", teams={"north": "team_lead"})
    north, south, unassigned = board.lead("north"), board.lead("south"), board.lead(None)
    teammate = board.user("north2@larkspur.example", teams={"north": "salesperson"})
    taken = board.lead("north")
    board.own(taken, teammate)
    seller = board.signed_in("seller@larkspur.example")
    lead = board.signed_in("lead@larkspur.example")

    def ids(answer: Any) -> set[str]:
        assert answer.status_code == 200, answer.text
        return {row["id"] for row in answer.json()}

    north_id, south_id = board.teams["north"], board.teams["south"]
    assert ids(seller.get(f"/api/leads?view=pool&team_id={north_id}")) == {str(north)}
    assert seller.get(f"/api/leads?view=pool&team_id={south_id}").status_code == 404
    assert seller.get(f"/api/leads?view=team&team_id={north_id}").status_code == 404
    assert seller.get("/api/leads?view=unassigned").status_code == 404
    assert seller.get(f"/api/leads/{south}").status_code == 404
    assert seller.get(f"/api/leads/{taken}").status_code == 404
    assert ids(lead.get(f"/api/leads?view=team&team_id={north_id}")) == {str(taken)}
    assert lead.get(f"/api/leads?view=team&team_id={south_id}").status_code == 404
    assert lead.get(f"/api/leads/{unassigned}").status_code == 404
    assert seller.get("/api/leads?view=nonsense").status_code == 422


def test_my_leads_lists_only_what_i_own(board: Board) -> None:
    me = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    mine, pool = board.lead("north"), board.lead("north")
    board.own(mine, me)
    client = board.signed_in(me.email)
    rows = client.get("/api/leads?view=mine").json()
    assert [row["id"] for row in rows] == [str(mine)]
    assert rows[0]["actions"] == ["release"]
    assert str(pool) not in {row["id"] for row in rows}


def test_the_lead_detail_shows_messages_events_and_the_viewers_actions(board: Board) -> None:
    board.user("seller@larkspur.example", teams={"north": "salesperson"})
    lead = board.lead("north", name="Ana Ruiz")
    detail = board.signed_in("seller@larkspur.example").get(f"/api/leads/{lead}").json()
    assert detail["state"] == "pool"
    assert detail["team"]["slug"] == "north"
    assert detail["actions"] == ["claim"]
    assert [e["type"] for e in detail["events"]] == ["lead.created"]
    assert detail["messages"][0]["name"] == "Ana Ruiz"
    assert "Is it still available?" in detail["messages"][0]["text"]


# --- one event per transition (F22) -----------------------------------------------------


def test_each_board_transition_writes_one_event_with_the_next_number(board: Board) -> None:
    lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("monitor@larkspur.example", roles=["monitor"])
    other = board.user("north2@larkspur.example", teams={"north": "salesperson"})
    as_lead = board.signed_in(lead_user.email)
    as_seller = board.signed_in(seller.email)
    as_monitor = board.signed_in("monitor@larkspur.example")
    lead = board.lead(None)
    north, east = str(board.teams["north"]), str(board.teams["east"])

    steps = [
        (as_monitor, "place", {"team_id": north}, "lead.placed", "pool"),
        (as_seller, "claim", None, "lead.claimed", "owned"),
        (as_seller, "release", None, "lead.released", "pool"),
        (as_lead, "claim", None, "lead.claimed", "owned"),
        (as_lead, "reassign", {"user_id": str(other.id)}, "lead.reassigned", "owned"),
        (as_lead, "return", None, "lead.returned", "pool"),
        (as_lead, "move", {"team_id": east}, "lead.moved_team", "pool"),
    ]
    for client, route, body, event, state in steps:
        answer = client.post(f"/api/leads/{lead}/{route}", json=body)
        assert answer.status_code == 200, (route, answer.text)
        assert answer.json()["event"] == event
        assert board.get(lead).state == state
    events = _events(board, lead)
    assert [e.seq for e in events] == list(range(1, len(steps) + 2))
    assert [e.type for e in events] == ["lead.created"] + [step[3] for step in steps]
    assert len({e.cause for e in events}) == len(events)


def test_a_refused_action_writes_no_event(board: Board) -> None:
    seller = board.user("seller@larkspur.example", teams={"north": "salesperson"})
    client = board.signed_in(seller.email)
    lead = board.lead("north")
    assert client.post(f"/api/leads/{lead}/release").status_code == 404
    assert client.post(f"/api/leads/{lead}/claim").status_code == 200
    assert client.post(f"/api/leads/{lead}/claim").status_code == 409
    assert [e.type for e in _events(board, lead)] == ["lead.created", "lead.claimed"]


def test_reassign_and_move_check_their_targets(board: Board) -> None:
    lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
    owner = board.user("north2@larkspur.example", teams={"north": "salesperson"})
    outsider = board.user("south1@larkspur.example", teams={"south": "salesperson"})
    client = board.signed_in(lead_user.email)
    lead = board.lead("north")
    board.own(lead, owner)
    for user_id in (outsider.id, owner.id, uuid.uuid4()):
        answer = client.post(f"/api/leads/{lead}/reassign", json={"user_id": str(user_id)})
        assert answer.status_code == 422
    for team_id in (board.teams["north"], uuid.uuid4()):
        answer = client.post(f"/api/leads/{lead}/move", json={"team_id": str(team_id)})
        assert answer.status_code == 422
    assert board.get(lead).owner_id == owner.id


def test_reassign_alerts_the_new_owner_and_move_alerts_the_new_pool(board: Board) -> None:
    lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
    owner = board.user("north2@larkspur.example", teams={"north": "salesperson"})
    new_owner = board.user("north3@larkspur.example", teams={"north": "salesperson"})
    east = board.user("east1@larkspur.example", teams={"east": "salesperson"})
    client = board.signed_in(lead_user.email)
    lead = board.lead("north")
    board.own(lead, owner)
    client.post(f"/api/leads/{lead}/reassign", json={"user_id": str(new_owner.id)})
    client.post(f"/api/leads/{lead}/move", json={"team_id": str(board.teams["east"])})
    with board.sessions() as session:
        alerts = {
            (a.kind, a.user_id)
            for a in session.scalars(select(Alert).where(Alert.lead_id == lead))
            if a.kind != "new_lead"
        }
    assert alerts == {("assigned", new_owner.id), ("move_team", east.id)}


# --- time to claim (F23) ----------------------------------------------------------------


def _claim_at(board: Board, client: Any, lead: uuid.UUID, at: datetime) -> dict[str, Any]:
    board.clock.current = at
    answer = client.post(f"/api/leads/{lead}/claim")
    assert answer.status_code == 200, answer.text
    return answer.json()["lead"]


def test_time_to_claim_in_business_and_wall_clock_minutes(board: Board) -> None:
    board.user("seller@larkspur.example", teams={"north": "salesperson"})
    client = board.signed_in("seller@larkspur.example")
    lead = board.lead("north")
    board.set_lead(lead, arrival_at=datetime(2026, 3, 2, 15, 0, tzinfo=UTC))  # Mon 10:00
    claimed = _claim_at(board, client, lead, datetime(2026, 3, 2, 15, 30, tzinfo=UTC))
    assert (claimed["claim_minutes"], claimed["claim_business_minutes"]) == (30, 30)


def test_after_hours_over_a_weekend_and_the_clock_change(board: Board) -> None:
    board.user("seller@larkspur.example", teams={"north": "salesperson"})
    client = board.signed_in("seller@larkspur.example")
    lead = board.lead("north")
    # Saturday 7 March 2026 at 19:00 EST, after closing; clocks go forward on the Sunday.
    board.set_lead(lead, arrival_at=datetime(2026, 3, 8, 0, 0, tzinfo=UTC))
    claimed = _claim_at(board, client, lead, datetime(2026, 3, 9, 13, 20, tzinfo=UTC))
    assert claimed["claim_business_minutes"] == 20  # Monday 09:00 to 09:20 EDT
    assert claimed["claim_minutes"] == (37 * 60) + 20


def test_a_return_to_pool_keeps_the_first_figures(board: Board) -> None:
    board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("lead@larkspur.example", teams={"north": "team_lead"})
    client = board.signed_in("seller@larkspur.example")
    lead = board.lead("north")
    board.set_lead(lead, arrival_at=datetime(2026, 3, 2, 15, 0, tzinfo=UTC))
    _claim_at(board, client, lead, datetime(2026, 3, 2, 15, 10, tzinfo=UTC))
    board.clock.current = datetime(2026, 3, 2, 17, 0, tzinfo=UTC)
    returned = board.signed_in("lead@larkspur.example").post(f"/api/leads/{lead}/return")
    assert returned.json()["lead"]["pool_since"] == "2026-03-02T17:00:00+00:00"
    again = _claim_at(board, client, lead, datetime(2026, 3, 2, 18, 0, tzinfo=UTC))
    assert again["claimed_at"] == "2026-03-02T15:10:00+00:00"
    assert (again["claim_minutes"], again["claim_business_minutes"]) == (10, 10)
    assert board.get(lead).claim_business_minutes == Decimal("10.00")


def test_placement_from_unassigned_keeps_the_arrival(board: Board) -> None:
    board.user("seller@larkspur.example", teams={"north": "salesperson"})
    board.user("monitor@larkspur.example", roles=["monitor"])
    client = board.signed_in("seller@larkspur.example")
    lead = board.lead(None)
    arrival = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
    board.set_lead(lead, arrival_at=arrival)
    board.clock.current = datetime(2026, 3, 2, 15, 40, tzinfo=UTC)
    monitor = board.signed_in("monitor@larkspur.example")
    placed = monitor.post(f"/api/leads/{lead}/place", json={"team_id": str(board.teams["north"])})
    assert placed.json()["lead"]["arrival_at"] == arrival.isoformat()
    claimed = _claim_at(board, client, lead, datetime(2026, 3, 2, 15, 45, tzinfo=UTC))
    assert claimed["claim_business_minutes"] == 45


# --- owner left (F25) -------------------------------------------------------------------


def test_deactivating_a_user_returns_their_leads_and_alerts_those_pools(board: Board) -> None:
    board.user("admin@larkspur.example", roles=["admin"])
    leaver = board.user(
        "leaver@larkspur.example", teams={"north": "salesperson", "south": "salesperson"}
    )
    north_mate = board.user("north2@larkspur.example", teams={"north": "salesperson"})
    south_mate = board.user("south1@larkspur.example", teams={"south": "salesperson"})
    leaver_client = board.signed_in(leaver.email)
    in_north, in_south, other = board.lead("north"), board.lead("south"), board.lead("north")
    board.own(in_north, leaver)
    board.own(in_south, leaver)
    board.set_lead(in_south, state="nurture")
    board.own(other, north_mate)

    admin = board.signed_in("admin@larkspur.example")
    answer = admin.patch(f"/api/users/{leaver.id}", json={"active": False})
    assert answer.status_code == 200
    assert answer.json()["active"] is False

    for lead in (in_north, in_south):
        assert (board.get(lead).state, board.get(lead).owner_id) == ("pool", None)
        last = _events(board, lead)[-1]
        assert (last.type, last.payload["reason"]) == ("lead.returned", "owner_left")
    assert board.get(other).owner_id == north_mate.id
    with board.sessions() as session:
        alerted = {
            (a.lead_id, a.user_id)
            for a in session.scalars(select(Alert).where(Alert.kind == "owner_left"))
        }
    assert alerted == {(in_north, north_mate.id), (in_south, south_mate.id)}
    assert leaver_client.get("/api/session").status_code == 401


def test_removing_a_user_from_a_team_returns_only_that_teams_leads(board: Board) -> None:
    board.user("admin@larkspur.example", roles=["admin"])
    mover = board.user(
        "mover@larkspur.example", teams={"north": "salesperson", "south": "salesperson"}
    )
    in_north, in_south = board.lead("north"), board.lead("south")
    board.own(in_north, mover)
    board.own(in_south, mover)
    admin = board.signed_in("admin@larkspur.example")
    south_only = {str(board.teams["south"]): "salesperson"}
    answer = admin.patch(f"/api/users/{mover.id}", json={"teams": south_only})
    assert answer.status_code == 200, answer.text
    assert answer.json()["teams"] == south_only
    assert board.get(in_north).state == "pool"
    assert board.get(in_south).owner_id == mover.id


def test_only_an_admin_manages_users(board: Board) -> None:
    board.user("admin@larkspur.example", roles=["admin"])
    lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
    admin = board.signed_in("admin@larkspur.example")
    lead_client = board.signed_in(lead_user.email)
    assert lead_client.get("/api/users").status_code == 404
    assert (
        lead_client.patch(f"/api/users/{lead_user.id}", json={"roles": ["admin"]}).status_code
        == 404
    )
    made = admin.post(
        "/api/users",
        json={
            "email": "New@Larkspur.example",
            "name": "New Person",
            "teams": {str(board.teams["north"]): "salesperson"},
        },
    )
    assert made.status_code == 201, made.text
    new_id = made.json()["id"]
    assert made.json()["email"] == "new@larkspur.example"
    assert (
        admin.post("/api/users", json={"email": "new@larkspur.example", "name": "X"}).status_code
        == 422
    )
    assert admin.put(f"/api/users/{new_id}/password", json={"password": "short"}).status_code == 422
    assert (
        admin.put(
            f"/api/users/{new_id}/password", json={"password": "a long enough one"}
        ).status_code
        == 204
    )
    assert (
        board.signed_in("new@larkspur.example", "a long enough one").get("/api/session").status_code
        == 200
    )
    assert admin.patch(f"/api/users/{new_id}", json={"roles": ["wizard"]}).status_code == 422
    assert admin.patch(f"/api/users/{new_id}", json={"name": " "}).status_code == 422
    renamed = admin.patch(f"/api/users/{new_id}", json={"name": "Renamed", "roles": ["monitor"]})
    assert (renamed.json()["name"], renamed.json()["roles"]) == ("Renamed", ["monitor"])
    assert admin.patch(f"/api/users/{uuid.uuid4()}", json={"name": "X"}).status_code == 404
    listed = {u["email"] for u in admin.get("/api/users").json()}
    assert {"admin@larkspur.example", "new@larkspur.example"} <= listed
    with board.sessions() as session:
        assert session.get_one(User, uuid.UUID(new_id)).roles == ["monitor"]


def test_a_team_lead_lists_their_team_for_reassign(board: Board) -> None:
    lead_user = board.user("lead@larkspur.example", teams={"north": "team_lead"})
    board.user("seller@larkspur.example", teams={"north": "salesperson"})
    client = board.signed_in(lead_user.email)
    north, south = board.teams["north"], board.teams["south"]
    names = {m["name"] for m in client.get(f"/api/teams/{north}/members").json()}
    assert names == {"Lead", "Seller"}
    assert client.get(f"/api/teams/{south}/members").status_code == 404
    assert client.get(f"/api/teams/{uuid.uuid4()}/members").status_code == 404
    teams = {t["slug"]: t["my_role"] for t in client.get("/api/teams").json()}
    assert teams["north"] == "team_lead"
    assert teams["south"] is None
