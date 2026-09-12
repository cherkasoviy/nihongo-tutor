"""``nihongo-content`` typer CLI. Phase 1 adds the kana importer; vocab/grammar arrive with Phase 2."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import text

from app import __version__
from app.config import get_settings
from app.content_pipeline.import_kana import import_kana
from app.db.base import get_engine, get_sessionmaker
from app.services.progress_transfer import TransferReport, export_progress, import_progress

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


@app.command("export-progress")
def export_progress_command(
    tg_user_id: Annotated[int, typer.Option("--tg-id", help="Telegram user id of the learner")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to write the export")] = Path("progress.json"),
) -> None:
    """Export one learner's progress to JSON, keyed by syllable rather than by row id.

    Card rows cannot be copied between databases: ``items.id`` is minted when the seed is imported,
    so the same 208 syllables carry different ids in every database. This travels by
    ``(script, char, direction)`` instead, and can be re-run right before a cutover.
    """

    async def _fetch() -> dict[str, object]:
        async with get_sessionmaker()() as session:
            return await export_progress(session, tg_user_id=tg_user_id)

    payload = asyncio.run(_fetch())
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    cards = payload["cards"]
    assert isinstance(cards, list)
    sessions = payload["sessions"]
    assert isinstance(sessions, list)
    streak = payload["streak"] or {}
    assert isinstance(streak, dict)
    typer.echo(
        f"wrote {out}: {len(cards)} cards, {sum(len(c['reviews']) for c in cards)} reviews, "
        f"{len(sessions)} sessions, streak {streak.get('current', 0)}"
    )


@app.command("import-progress")
def import_progress_command(
    source: Annotated[Path, typer.Option("--in", "-i", help="Export file to apply")],
) -> None:
    """Apply an exported progress file to this database. Safe to re-run.

    Requires the kana seed to be imported first, so the syllables can be resolved.
    """
    payload = json.loads(source.read_text(encoding="utf-8"))

    async def _apply() -> TransferReport:
        async with get_sessionmaker()() as session:
            report = await import_progress(session, payload)
            await session.commit()
            return report

    report = asyncio.run(_apply())
    typer.echo(
        f"applied {source}: {report.cards} cards, {report.reviews} new reviews, " f"{report.sessions} new sessions"
    )
    if report.skipped_unknown_syllables:
        typer.echo(
            f"WARNING: {report.skipped_unknown_syllables} syllables in the export are not in this "
            f"database — run `import-kana` first",
            err=True,
        )
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
