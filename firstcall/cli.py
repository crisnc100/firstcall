"""The firstcall command line."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Firstcall: one queue, a fast alert and one owner for every lead."""


@app.command()
def api(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Serve the HTTP app."""
    import uvicorn

    from firstcall.api import create_app

    uvicorn.run(create_app(), host=host, port=port)


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
    """Issue a new token or password for a source. It is shown once."""
    from firstcall.seed import set_secret

    typer.echo(set_secret(_sessions(), slug))


@app.command()
def worker(until_idle: bool = typer.Option(False, help="Stop when the queue is empty.")) -> None:
    """Process stored messages."""
    from firstcall.pipeline.worker import work
    from firstcall.runtime import SystemClock
    from firstcall.settings import Settings

    work(_sessions(), SystemClock(), Settings(), until_idle=until_idle)


@app.command()
def scheduler() -> None:
    """Run `tick` every 30 seconds."""
    from firstcall.pipeline.worker import schedule
    from firstcall.runtime import SystemClock
    from firstcall.settings import Settings

    schedule(_sessions(), SystemClock(), Settings())


@app.command()
def tick() -> None:
    """One scheduler pass: return jobs whose lease ran out."""
    from firstcall.pipeline.worker import tick as run_tick
    from firstcall.runtime import SystemClock
    from firstcall.settings import Settings

    typer.echo(run_tick(_sessions(), SystemClock(), Settings()))


@app.command()
def health(kind: str = typer.Argument(..., help="worker or scheduler")) -> None:
    """Exit 0 when this host's process beat in the last 60 seconds."""
    from firstcall.pipeline.worker import healthy

    raise typer.Exit(0 if healthy(_sessions(), kind) else 1)


@app.command("eval")
def eval_command(
    seed: int = typer.Option(..., help="7 (tune) or 11 (held out); 13 waits for the field methods"),
    date: str = typer.Option("", help="The report date, YYYY-MM-DD; today when empty"),
) -> None:
    """Run the rules-only eval and write evals/reports/<date>-rules-seed-<N>.md and .json."""
    from datetime import UTC, datetime

    from firstcall.evaluation.report import write
    from firstcall.evaluation.run import evaluate
    from firstcall.settings import Settings

    report = evaluate(seed, Settings().database_url)
    day = date or datetime.now(UTC).date().isoformat()
    for path in write(report, day):
        typer.echo(f"wrote {path}")


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


if __name__ == "__main__":
    app()
