"""The firstcall command line."""

from __future__ import annotations

import typer

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
