"""The firstcall command line."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import typer

if TYPE_CHECKING:
    import uvicorn
    from sqlalchemy.orm import Session, sessionmaker

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Firstcall: one queue, a fast alert and one owner for every lead."""


def server_config(asgi: Any, host: str, port: int) -> uvicorn.Config:
    """The server settings. Proxy headers are off, so FIRSTCALL_TRUST_PROXY is the only
    switch that makes the app read X-Forwarded-For. The access log is off because a form
    source's key sits in its URL path, and an access log would print it on every post."""
    import uvicorn

    return uvicorn.Config(asgi, host=host, port=port, proxy_headers=False, access_log=False)


@app.command()
def api(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Serve the HTTP app."""
    import uvicorn

    from firstcall.api import create_app

    uvicorn.Server(server_config(create_app(), host, port)).run()


def _sessions() -> sessionmaker[Session]:
    from firstcall.db import make_engine, make_sessions
    from firstcall.settings import Settings

    return make_sessions(make_engine(Settings().database_url))


@app.command("seed")
def seed_command() -> None:
    """Load the demo company from config/demo.yaml."""
    from firstcall.seed import seed

    org_id = seed(_sessions())
    typer.echo(f"seeded org {org_id}")


@app.command("rotate-secret")
def rotate_secret(slug: str) -> None:
    """Issue a new form key or password for a source. It is shown once."""
    from firstcall.seed import set_secret

    typer.echo(set_secret(_sessions(), slug))


@app.command()
def worker(until_idle: bool = typer.Option(False, help="Stop when the queue is empty.")) -> None:
    """Process stored messages."""
    from firstcall.outbound.dispatch import Outbound
    from firstcall.pipeline.worker import model_reader, work
    from firstcall.runtime import SystemClock
    from firstcall.settings import Settings

    sessions, clock, settings = _sessions(), SystemClock(), Settings()
    reader = model_reader(sessions, clock, settings)
    outbound = Outbound.from_settings(sessions, clock, settings)
    work(sessions, clock, settings, until_idle=until_idle, reader=reader, outbound=outbound)


@app.command()
def scheduler() -> None:
    """Run `tick` every 30 seconds."""
    from firstcall.outbound.dispatch import Outbound
    from firstcall.pipeline.worker import model_reader, schedule
    from firstcall.runtime import SystemClock
    from firstcall.settings import Settings

    sessions, clock, settings = _sessions(), SystemClock(), Settings()
    outbound = Outbound.from_settings(sessions, clock, settings)
    schedule(sessions, clock, settings, model_reader(sessions, clock, settings), outbound)


@app.command()
def tick() -> None:
    """One scheduler pass: reap expired jobs, escalate, send due alerts, then reread
    rules-path messages."""
    from firstcall.outbound.dispatch import Outbound
    from firstcall.pipeline.worker import alert_tick, followup_tick, model_reader
    from firstcall.pipeline.worker import tick as run_tick
    from firstcall.runtime import SystemClock
    from firstcall.settings import Settings

    sessions, clock, settings = _sessions(), SystemClock(), Settings()
    outbound = Outbound.from_settings(sessions, clock, settings)
    reader = model_reader(sessions, clock, settings)
    typer.echo(
        run_tick(sessions, clock, settings, reader)
        | followup_tick(sessions, clock)
        | alert_tick(sessions, clock, outbound)
    )


@app.command()
def health(kind: str = typer.Argument(..., help="worker or scheduler")) -> None:
    """Exit 0 when this host's process beat in the last 60 seconds."""
    from firstcall.pipeline.worker import healthy

    raise typer.Exit(0 if healthy(_sessions(), kind) else 1)


MODES = ("rules", "replay", "record", "live")
MAX_RUN_BUDGET = 10.0


@app.command("eval")
def eval_command(
    seed: int = typer.Option(..., help="7 (tune), 11 (held out) or 13 (confirm, once)"),
    date: str = typer.Option("", help="The report date, YYYY-MM-DD; today when empty"),
    mode: str = typer.Option(
        "rules", help="rules (no model), replay (recorded answers), record or live (paid)"
    ),
    budget: float = typer.Option(1.0, help="Most this run may spend, in US dollars (<= $10)"),
    model: str = typer.Option("claude-haiku-5-5", help="The model, for every mode but rules"),
) -> None:
    """Run an eval and write its reports under evals/reports/.

    rules writes <date>-rules-seed-<N>; the model modes write <date>-model-seed-<N> for
    seeds 7 and 11 and <date>-confirm-seed-13 for the confirm run. record spends money: it
    records every answer first, then writes the report from the recordings. A paid mode reads
    the key from FIRSTCALL_MODEL_KEY; load it from .env with `set -a; . ./.env; set +a`."""
    from dataclasses import replace
    from datetime import UTC, datetime
    from decimal import Decimal

    from firstcall.settings import Settings

    if mode not in MODES:
        raise typer.BadParameter(f"mode must be one of {MODES}")
    day = date or datetime.now(UTC).date().isoformat()
    settings = Settings()
    if mode == "rules":
        from firstcall.evaluation.report import write
        from firstcall.evaluation.run import evaluate

        for path in write(evaluate(seed, settings.database_url), day):
            typer.echo(f"wrote {path}")
        return

    from firstcall.evaluation.model_eval import (
        CONFIRM_SEED,
        ModelRun,
        evaluate_confirm,
        evaluate_models,
        record,
    )
    from firstcall.evaluation.model_report import write_confirm, write_model
    from firstcall.evaluation.run import FIELD_METHODS, REPO_ROOT, field_methods_frozen
    from firstcall.pipeline.model_read import load_field_methods

    key = settings.model_key.get_secret_value()
    if mode in ("record", "live") and not key:
        raise typer.BadParameter(
            f"--mode {mode} needs FIRSTCALL_MODEL_KEY; load it with `set -a; . ./.env; set +a`"
        )
    if budget > MAX_RUN_BUDGET:
        raise typer.BadParameter(f"--budget is at most ${MAX_RUN_BUDGET:.0f} for one run")
    if seed == CONFIRM_SEED and not field_methods_frozen(FIELD_METHODS, REPO_ROOT):
        raise typer.BadParameter("seed 13 runs only after config/field_methods.yaml is committed")
    run = ModelRun(mode=mode, model=model, budget_usd=Decimal(str(budget)), key=key)
    url = settings.database_url
    if mode == "record":
        spent = record(seed, url, run)
        typer.echo(f"recorded seed {seed}: ${spent:.4f} spent of ${budget:.2f}")
        run = replace(run, mode="replay")
    if seed == CONFIRM_SEED:
        paths = write_confirm(evaluate_confirm(seed, url, run, load_field_methods()), day)
    else:
        paths = write_model(evaluate_models(seed, url, run), day)
    for path in paths:
        typer.echo(f"wrote {path}")


user_app = typer.Typer(no_args_is_help=True, help="Users and passwords.")
app.add_typer(user_app, name="user")


def _one_org(session: Session) -> Any:
    from sqlalchemy import select

    from firstcall.db import Org

    orgs = list(session.scalars(select(Org)))
    if len(orgs) != 1:
        raise typer.BadParameter("these commands expect exactly one org; run `firstcall seed`")
    return orgs[0]


@user_app.command("add")
def user_add(
    email: str,
    name: str = typer.Option(..., help="The name the board shows"),
    role: Annotated[
        list[str] | None, typer.Option(help="An org role: monitor or admin; repeatable")
    ] = None,
    team: Annotated[
        list[str] | None, typer.Option(help="slug=salesperson or slug=team_lead; repeatable")
    ] = None,
) -> None:
    """Add a user with no password. Set one with `firstcall user password`."""
    from sqlalchemy import select

    from firstcall.board import accounts
    from firstcall.db import Team
    from firstcall.runtime import SystemClock

    with _sessions().begin() as session:
        org = _one_org(session)
        slugs = {t.slug: t.id for t in session.scalars(select(Team).where(Team.org_id == org.id))}
        wanted = {}
        for entry in team or []:
            slug, sep, team_role = entry.partition("=")
            if not sep:
                raise typer.BadParameter(f"--team takes slug=role, not {entry}")
            if slug not in slugs:
                raise typer.BadParameter(f"no team {slug}")
            wanted[slugs[slug]] = team_role
        try:
            user = accounts.create_user(session, org.id, email, name, role or [])
            accounts.set_memberships(session, user, wanted, SystemClock())
        except accounts.AccountError as error:
            raise typer.BadParameter(str(error)) from error
        typer.echo(f"added {user.email}")


@user_app.command("password")
def user_password(email: str) -> None:
    """Set a user's password. It is typed at a prompt, never passed as an argument."""
    from sqlalchemy import select

    from firstcall.board import accounts
    from firstcall.db import User

    with _sessions().begin() as session:
        org = _one_org(session)
        address = accounts.normal_email(email)
        user = session.scalar(select(User).where(User.org_id == org.id, User.email == address))
        if user is None:
            raise typer.BadParameter(f"no user {address}")
        password = typer.prompt("New password", hide_input=True, confirmation_prompt=True)
        try:
            accounts.set_password(session, user, password)
        except accounts.AccountError as error:
            raise typer.BadParameter(str(error)) from error
        typer.echo(f"password set for {user.email}")


corpus_app = typer.Typer(no_args_is_help=True, help="The synthetic corpus.")
app.add_typer(corpus_app, name="corpus")


@corpus_app.command("generate")
def corpus_generate(seed: int = typer.Option(..., help="7, 11 or 13")) -> None:
    """Write evals/gold/seed-N and record its hashes in the manifest."""
    from firstcall.corpus.generate import SEEDS, write_seed
    from firstcall.corpus.manifest import record_seed

    if seed not in SEEDS:
        raise typer.BadParameter(f"seed must be one of {SEEDS}")
    hashes = write_seed(seed)
    record_seed(seed, hashes)
    for name, digest in hashes.items():
        typer.echo(f"seed {seed} {name} {digest}")


@corpus_app.command("notes")
def corpus_notes(seed: int = typer.Option(..., help="7 or 11")) -> None:
    """Write evals/gold/notes/seed-N and record its hashes in the notes manifest."""
    from firstcall.corpus.notes import NOTE_SEEDS, record_notes_seed, write_notes

    if seed not in NOTE_SEEDS:
        raise typer.BadParameter(f"seed must be one of {NOTE_SEEDS}")
    hashes = write_notes(seed)
    record_notes_seed(seed, hashes)
    for name, digest in hashes.items():
        typer.echo(f"notes seed {seed} {name} {digest}")


if __name__ == "__main__":
    app()
