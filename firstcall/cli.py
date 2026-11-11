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
