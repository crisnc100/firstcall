"""The user commands: how the first admin and the demo cast get in before any page exists."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from firstcall.board import accounts
from firstcall.cli import app
from firstcall.db import Membership, Team, User
from firstcall.seed import seed


@pytest.fixture
def cli_db(
    sessions: sessionmaker[Session], database_url: str, monkeypatch: pytest.MonkeyPatch
) -> sessionmaker[Session]:
    seed(sessions)
    monkeypatch.setenv("FIRSTCALL_DATABASE_URL", database_url)
    return sessions


def test_add_a_user_with_roles_and_teams_then_set_a_password(
    cli_db: sessionmaker[Session],
) -> None:
    runner = CliRunner()
    added = runner.invoke(
        app,
        [
            "user", "add", "Lee@Larkspur.example", "--name", "Lee Park",
            "--role", "admin", "--team", "north=team_lead",
        ],
    )  # fmt: skip
    assert added.exit_code == 0, added.output
    password = runner.invoke(
        app,
        ["user", "password", "lee@larkspur.example"],
        input="a long enough one\na long enough one\n",
    )
    assert password.exit_code == 0, password.output
    assert "a long enough one" not in password.output
    with cli_db() as session:
        user = session.scalars(select(User)).one()
        role = session.scalar(
            select(Membership.role)
            .join(Team, Team.id == Membership.team_id)
            .where(Membership.user_id == user.id, Team.slug == "north")
        )
        assert (user.email, user.roles, role) == ("lee@larkspur.example", ["admin"], "team_lead")
        assert accounts.verify(session, "lee@larkspur.example", "a long enough one") is not None


def test_bad_input_is_refused_with_a_plain_message(cli_db: sessionmaker[Session]) -> None:
    runner = CliRunner()
    for args, message in (
        (
            ["user", "add", "a@larkspur.example", "--name", "A", "--team", "nowhere=salesperson"],
            "no team",
        ),
        (["user", "add", "a@larkspur.example", "--name", "A", "--team", "north"], "slug=role"),
        (["user", "add", "a@larkspur.example", "--name", "A", "--role", "wizard"], "unknown roles"),
        (["user", "password", "nobody@larkspur.example"], "no user"),
    ):
        result = runner.invoke(app, args, input="a long enough one\na long enough one\n")
        assert result.exit_code != 0, args
        assert message in result.output, (args, result.output)
    short = runner.invoke(app, ["user", "add", "b@larkspur.example", "--name", "B"])
    assert short.exit_code == 0
    refused = runner.invoke(app, ["user", "password", "b@larkspur.example"], input="short\nshort\n")
    assert refused.exit_code != 0
    assert "at least 12" in refused.output
