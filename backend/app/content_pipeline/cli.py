"""``nihongo-content`` typer CLI. Phase 1 adds the kana importer; vocab/grammar arrive with Phase 2."""

from __future__ import annotations

import asyncio

import typer
from sqlalchemy import text

from app import __version__
from app.config import get_settings
from app.content_pipeline.import_kana import import_kana
from app.db.base import get_engine, get_sessionmaker

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


@app.command("import-kana")
def import_kana_command() -> None:
    """Import (or refresh) the kana seed. Safe to re-run: upserts on the natural keys."""

    async def _run() -> None:
        async with get_sessionmaker()() as session:
            report = await import_kana(session)
            await session.commit()
        typer.echo(f"kana rows upserted: {report.kana_seen}; item rows upserted: {report.items_seen}")

    asyncio.run(_run())


if __name__ == "__main__":
    app()
