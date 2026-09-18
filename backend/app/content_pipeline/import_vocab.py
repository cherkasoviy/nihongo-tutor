"""Import the vocabulary seed into ``vocab_lemmas`` + ``items``.

Idempotent on the natural keys, exactly as ``import_kana.py`` is: ``slug`` for a lemma,
``(type, ref_id)`` for its item hub row. Re-importing refreshes rows in place — a new id would
orphan every card a learner has built on that word.

The one thing this importer does that the kana one does not is enforce the review gate.
``items.active`` follows ``gloss_review_status``, so an entry nobody has read yet is in the
database but invisible to the planner. ``--include-unreviewed`` lifts that for local work.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.content_pipeline.vocab_seed import VocabSeedFile, load_vocab
from app.db.models.content import Item, ItemStage, ItemType, Vocab
from app.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class VocabImportReport:
    vocab_seen: int = 0
    items_seen: int = 0
    active: int = 0

    def __add__(self, other: VocabImportReport) -> VocabImportReport:
        return VocabImportReport(
            vocab_seen=self.vocab_seen + other.vocab_seen,
            items_seen=self.items_seen + other.items_seen,
            active=self.active + other.active,
        )


async def import_seed_file(
    session: AsyncSession,
    seed: VocabSeedFile,
    *,
    include_unreviewed: bool = False,
) -> VocabImportReport:
    report = VocabImportReport()

    for entry in seed.vocab:
        values = {
            "slug": entry.slug,
            "word": entry.word,
            "reading": entry.reading,
            "gloss_ru": entry.gloss_ru,
            "pos": entry.pos,
            "tags": list(entry.tags),
            "example_ja": entry.example.ja,
            "example_reading": entry.example.reading,
            "example_gloss_ru": entry.example.gloss_ru,
            "gloss_source": entry.gloss_source,
            "gloss_review_status": entry.gloss_review_status,
        }
        # The enrichment columns are absent from `values` on purpose: this seed does not author
        # them, and listing them would overwrite whatever `build_vocab` had filled in on a
        # re-import. docs/CONTENT.md: the pipeline extends these rows, it does not regenerate them.
        vocab_stmt = (
            insert(Vocab)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[Vocab.slug],
                set_={k: v for k, v in values.items() if k != "slug"},
            )
            .returning(Vocab.id)
        )
        vocab_id = (await session.execute(vocab_stmt)).scalar_one()

        active = include_unreviewed or entry.is_approved
        item_values = {
            "type": ItemType.vocab,
            "ref_id": vocab_id,
            "slug": entry.slug,
            "curriculum_order": entry.curriculum_order,
            "stage": ItemStage.core,
            "active": active,
        }
        item_stmt = (
            insert(Item)
            .values(**item_values)
            .on_conflict_do_update(
                index_elements=[Item.type, Item.ref_id],
                # `active` is in the update set deliberately. Demoting an entry to `rejected` in the
                # seed has to take it away again on the next import, and a local run with
                # --include-unreviewed must not leave production's rows switched on.
                set_={k: v for k, v in item_values.items() if k not in ("type", "ref_id")},
            )
        )
        await session.execute(item_stmt)
        report += VocabImportReport(vocab_seen=1, items_seen=1, active=int(active))

    return report


async def import_vocab(
    session: AsyncSession,
    *,
    seed_dir: Path | None = None,
    include_unreviewed: bool = False,
) -> VocabImportReport:
    """Import the vocabulary seed. The caller owns the transaction."""
    seed = load_vocab(seed_dir=seed_dir)
    report = await import_seed_file(session, seed, include_unreviewed=include_unreviewed)
    log.info(
        "vocab seed imported",
        count=report.vocab_seen,
        active=report.active,
        include_unreviewed=include_unreviewed,
    )
    return report
