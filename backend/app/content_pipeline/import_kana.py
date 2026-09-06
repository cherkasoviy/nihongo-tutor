"""Import the kana seed into ``kana`` + ``items``.

Idempotent by design: the plan requires the content pipeline to be re-runnable ("idempotent upserts,
``--only-missing`` default"), so importing twice must not duplicate rows or renumber the curriculum.
Conflicts resolve on the natural keys — ``(script, char)`` for a syllable, ``(type, ref_id)`` for its
item hub row.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.content_pipeline.kana_seed import KanaSeedFile, load_all
from app.db.models.content import Item, ItemStage, ItemType, Kana, KanaScript
from app.logging import get_logger

log = get_logger(__name__)

_STAGE_BY_SCRIPT = {
    KanaScript.hiragana: ItemStage.kana_hira,
    KanaScript.katakana: ItemStage.kana_kata,
}


@dataclass(frozen=True, slots=True)
class ImportReport:
    kana_seen: int = 0
    items_seen: int = 0

    def __add__(self, other: ImportReport) -> ImportReport:
        return ImportReport(self.kana_seen + other.kana_seen, self.items_seen + other.items_seen)


def item_slug(script: KanaScript, row: str, romaji_key: str) -> str:
    """Stable, human-readable identity for a kana item. ``romaji_key`` never reaches the learner.

    The row is part of the slug because ``romaji_key`` alone is not unique: modern Japanese merged
    ぢ into じ and づ into ず, so each script has two syllables keyed ``ji`` and two keyed ``zu``.
    They differ only by the row they come from, and they are genuinely different characters a
    learner has to recognise separately.
    """
    return f"kana:{script.value}:{row}:{romaji_key}"


async def import_seed_file(session: AsyncSession, seed: KanaSeedFile) -> ImportReport:
    stage = _STAGE_BY_SCRIPT[seed.script]
    report = ImportReport()

    for entry in seed.kana:
        values = {
            "char": entry.char,
            "script": seed.script,
            "romaji_key": entry.romaji_key,
            "cyrillic": entry.cyrillic,
            "row": entry.row,
            "kind": entry.kind,
            "group_order": entry.group_order,
            "mnemonic_ru": entry.mnemonic_ru,
            "example_word": entry.example_word,
            "example_reading": entry.example_reading,
            "example_gloss_ru": entry.example_gloss_ru,
        }
        kana_stmt = (
            insert(Kana)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[Kana.script, Kana.char],
                set_={k: v for k, v in values.items() if k not in ("char", "script")},
            )
            .returning(Kana.id)
        )
        kana_id = (await session.execute(kana_stmt)).scalar_one()

        item_values = {
            "type": ItemType.kana,
            "ref_id": kana_id,
            "slug": item_slug(seed.script, entry.row, entry.romaji_key),
            "curriculum_order": entry.group_order,
            "stage": stage,
            "active": True,
        }
        item_stmt = (
            insert(Item)
            .values(**item_values)
            .on_conflict_do_update(
                index_elements=[Item.type, Item.ref_id],
                set_={
                    "slug": item_values["slug"],
                    "curriculum_order": item_values["curriculum_order"],
                    "stage": item_values["stage"],
                    "active": True,
                },
            )
        )
        await session.execute(item_stmt)
        report += ImportReport(kana_seen=1, items_seen=1)

    return report


async def import_kana(session: AsyncSession, *, seed_dir: Path | None = None) -> ImportReport:
    """Import every script. The caller owns the transaction."""
    report = ImportReport()
    for seed in load_all(seed_dir=seed_dir):
        file_report = await import_seed_file(session, seed)
        log.info("kana seed imported", script=seed.script.value, count=file_report.kana_seen)
        report += file_report
    return report
