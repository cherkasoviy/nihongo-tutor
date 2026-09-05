"""``nihongo-content`` typer CLI. Phase 0 exposes only ``check``; importers arrive with Phase 1/2."""

from __future__ import annotations

import asyncio

import typer
from sqlalchemy import text

from app import __version__
from app.config import get_settings
from app.db.base import get_engine

app = typer.Typer(help="Nihongo Tutor content pipeline", no_args_is_help=True)


@app.command()
def version() -> None:
    typer.echo(__version__)


@app.command()
def check() -> None:
    """Verify database connectivity and print the configured models."""
    settings = get_settings()

    async def _run() -> None:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))

    asyncio.run(_run())
    typer.echo(f"db ok; MODEL_STRONG={settings.model_strong} MODEL_FAST={settings.model_fast}")


if __name__ == "__main__":
    app()
