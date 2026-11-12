"""Sign-in: passwords, the rate limit, cookie flags, CSRF and sign-out (F19)."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from firstcall.board import accounts
from firstcall.db import User, UserSession
from firstcall.settings import Settings
from tests.board_helpers import PASSWORD, Board
from tests.conftest import FakeClock


@pytest.fixture
def board(sessions: sessionmaker[Session], clock: FakeClock) -> Board:
    return Board(sessions, clock)


def _try(board: Board, email: str, password: str, ip: str | None = None) -> int:
    client = board.client()
    csrf = client.get("/api/csrf").json()["csrf"]
    headers = {"X-CSRF-Token": csrf}
    if ip:
        headers["X-Forwarded-For"] = ip
    return client.post(
        "/api/session", json={"email": email, "password": password}, headers=headers
    ).status_code


def test_the_password_is_stored_as_argon2id(board: Board) -> None:
    user = board.user("sam@larkspur.example", teams={"north": "salesperson"})
    assert user.password_hash is not None
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash


def test_a_right_password_signs_in_and_a_wrong_one_does_not(board: Board) -> None:
    board.user("sam@larkspur.example", teams={"north": "salesperson"})
    assert _try(board, "sam@larkspur.example", "not the password") == 401
    assert _try(board, "nobody@larkspur.example", PASSWORD) == 401
    assert _try(board, "  SAM@Larkspur.example ", PASSWORD) == 200


def test_a_user_with_no_password_or_deactivated_cannot_sign_in(board: Board) -> None:
    board.user("new@larkspur.example", password=None)
    assert _try(board, "new@larkspur.example", "") == 401
    gone = board.user("gone@larkspur.example")
    with board.sessions.begin() as session:
        accounts.deactivate(session, session.get_one(User, gone.id), board.clock)
    assert _try(board, "gone@larkspur.example", PASSWORD) == 401


def test_a_short_password_is_refused(board: Board) -> None:
    with pytest.raises(accounts.AccountError, match="at least 12"):
        board.user("short@larkspur.example", password="short")


def test_ten_tries_a_minute_per_address_then_429(board: Board) -> None:
    board.user("sam@larkspur.example")
    for n in range(10):
        assert _try(board, f"guess{n}@larkspur.example", "wrong-password") == 401
    assert _try(board, "sam@larkspur.example", PASSWORD) == 429
    board.monotonic.value += 61
    assert _try(board, "sam@larkspur.example", PASSWORD) == 200


def test_ten_tries_a_minute_per_email_across_addresses(sessions: sessionmaker[Session]) -> None:
    board = Board(sessions, FakeClock(), Settings(env="test", trust_proxy=True))
    board.user("sam@larkspur.example")
    for n in range(10):
        assert _try(board, "sam@larkspur.example", "wrong-password", ip=f"198.51.100.{n}") == 401
    assert _try(board, "sam@larkspur.example", PASSWORD, ip="203.0.113.9") == 429
    assert _try(board, "other@larkspur.example", "wrong-password", ip="203.0.113.9") == 401


def test_the_session_cookie_is_httponly_lax_and_secure_outside_local(
    sessions: sessionmaker[Session],
) -> None:
    for env, secure in (("test", True), ("local", False)):
        board = Board(sessions, FakeClock(), Settings(env=env))  # type: ignore[arg-type]
        board.user(f"{env}@larkspur.example")
        client = board.client()
        csrf = client.get("/api/csrf").json()["csrf"]
        answer = client.post(
            "/api/session",
            json={"email": f"{env}@larkspur.example", "password": PASSWORD},
            headers={"X-CSRF-Token": csrf},
        )
        cookie = next(
            c for c in answer.headers.get_list("set-cookie") if c.startswith("fc_session=")
        )
        flags = {part.strip().split("=")[0].lower() for part in cookie.split(";")}
        assert "httponly" in flags
        assert "samesite=lax" in cookie.lower()
        assert ("secure" in flags) is secure
        assert "Max-Age=604800" in cookie


def test_the_cookie_holds_a_token_the_database_keeps_only_hashed(board: Board) -> None:
    board.user("sam@larkspur.example")
    client = board.signed_in("sam@larkspur.example")
    token = client.cookies["fc_session"]
    with board.sessions() as session:
        stored = session.scalars(select(UserSession.token_hash)).one()
    assert stored == accounts.hash_token(token)
    assert token not in stored


def test_sign_in_without_the_csrf_token_is_refused(board: Board) -> None:
    board.user("sam@larkspur.example")
    client = board.client()
    client.get("/api/csrf")
    answer = client.post(
        "/api/session", json={"email": "sam@larkspur.example", "password": PASSWORD}
    )
    assert answer.status_code == 403
    wrong = client.post(
        "/api/session",
        json={"email": "sam@larkspur.example", "password": PASSWORD},
        headers={"X-CSRF-Token": "made-up"},
    )
    assert wrong.status_code == 403


def test_a_state_change_without_the_session_csrf_token_is_refused(board: Board) -> None:
    board.user("sam@larkspur.example", teams={"north": "salesperson"})
    lead = board.lead("north")
    client = board.signed_in("sam@larkspur.example")
    token = client.headers.pop("X-CSRF-Token")
    assert client.post(f"/api/leads/{lead}/claim").status_code == 403
    assert client.post(f"/api/leads/{lead}/claim", headers={"X-CSRF-Token": "x"}).status_code == 403
    assert client.delete("/api/session").status_code == 403
    assert board.get(lead).state == "pool"
    assert client.get(f"/api/leads/{lead}").status_code == 200
    ok = client.post(f"/api/leads/{lead}/claim", headers={"X-CSRF-Token": token})
    assert ok.status_code == 200


def test_sign_out_ends_the_session_for_good(board: Board) -> None:
    board.user("sam@larkspur.example")
    client = board.signed_in("sam@larkspur.example")
    token = client.cookies["fc_session"]
    assert client.get("/api/session").status_code == 200
    answer = client.delete("/api/session")
    assert answer.status_code == 204
    assert 'fc_session=""' in answer.headers["set-cookie"]
    with board.sessions() as session:
        assert session.scalar(select(func.count()).select_from(UserSession)) == 0
    replay = board.client()
    replay.cookies.set("fc_session", token)
    assert replay.get("/api/session").status_code == 401


def test_a_session_expires_and_no_cookie_means_401(board: Board) -> None:
    board.user("sam@larkspur.example")
    client = board.signed_in("sam@larkspur.example")
    assert board.client().get("/api/leads?view=mine").status_code == 401
    board.clock.advance(days=7, seconds=1)
    assert client.get("/api/session").status_code == 401
