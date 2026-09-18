"""Database → ``vocab_core.json``. The other half of rule 4 in docs/CONTENT.md.

Import alone would mean that the day someone edits a gloss directly in Postgres, that edit exists
in exactly one place. So the pipeline runs both ways, and ``git diff`` after an export is the audit:
empty means the database and the repo agree.

Two things about the output format:

- **Field order is the authored order, not alphabetical.** Alphabetical would sort ``curriculum_order``
  above ``word`` and turn every export into a 4,000-line diff, which destroys the only thing the
  audit is for. The order below is the file's own, so a clean database exports byte-for-byte.
- **The file header is carried over from disk.** ``version``, ``kind`` and the prose ``note`` describe
  the file rather than any row, and no column holds them. They are preserved the way a formatter
  preserves comments; the entries are what this command regenerates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content_pipeline.vocab_seed import SEED_DIR, SEED_FILE
from app.db.models.content import Item, ItemType, Vocab

_DEFAULT_HEADER: Final[dict[str, Any]] = {"version": 1, "kind": "vocab"}


def _header(path: Path) -> dict[str, Any]:
    """``version``, ``kind`` and ``note`` from the file on disk, if it is readable."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(_DEFAULT_HEADER)
    return {key: raw[key] for key in ("version", "kind", "note") if key in raw}


async def export_vocab(session: AsyncSession, *, seed_dir: Path | None = None) -> str:
    """Render every vocabulary row as the seed file's exact text, trailing newline included."""
    path = (seed_dir or SEED_DIR) / SEED_FILE

    rows = (
        await session.execute(
            select(Vocab, Item.curriculum_order)
            .join(Item, (Item.ref_id == Vocab.id) & (Item.type == ItemType.vocab))
            .order_by(Item.curriculum_order)
        )
    ).all()

    entries = [
        {
            "slug": vocab.slug,
            "word": vocab.word,
            "reading": vocab.reading,
            "gloss_ru": vocab.gloss_ru,
            "pos": vocab.pos,
            "curriculum_order": curriculum_order,
            "tags": list(vocab.tags),
            "example": {
                "ja": vocab.example_ja,
                "reading": vocab.example_reading,
                "gloss_ru": vocab.example_gloss_ru,
            },
            "gloss_source": vocab.gloss_source,
            "gloss_review_status": vocab.gloss_review_status,
        }
        for vocab, curriculum_order in rows
    ]

    payload = {**_header(path), "vocab": entries}
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
