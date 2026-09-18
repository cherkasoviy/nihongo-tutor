"""The vocabulary import: the review gate, idempotency, and the export audit.

The gate is the part worth testing hardest. docs/CONTENT.md rule 3 exists because a learner cannot
detect a subtly wrong Japanese sentence — they will simply learn it — so an entry nobody has read
must be in the database and unreachable by the planner.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content_pipeline.export_vocab import export_vocab
from app.content_pipeline.import_vocab import import_vocab
from app.content_pipeline.vocab_seed import SEED_DIR, SEED_FILE, load_vocab
from app.db.models.content import Item, ItemStage, ItemType, Vocab

pytestmark = pytest.mark.integration

EXPECTED = 220


async def _count(session: AsyncSession, model: type[Vocab] | type[Item]) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


def _seed_copy(tmp_path: Path, mutate: Any = None) -> Path:
    seed_dir = tmp_path / "seed"
    shutil.copytree(SEED_DIR, seed_dir)
    if mutate is not None:
        path = seed_dir / SEED_FILE
        data = json.loads(path.read_text(encoding="utf-8"))
        mutate(data)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return seed_dir


async def test_import_creates_every_entry_and_its_item(session: AsyncSession) -> None:
    report = await import_vocab(session)
    await session.commit()

    assert report.vocab_seen == EXPECTED
    assert await _count(session, Vocab) == EXPECTED
    assert await _count(session, Item) == EXPECTED

    items = list(await session.scalars(select(Item)))
    assert {item.type for item in items} == {ItemType.vocab}
    assert {item.stage for item in items} == {ItemStage.core}
    assert sorted(item.curriculum_order for item in items) == list(range(1, EXPECTED + 1))
    # The hub row's slug is the lemma's slug: one identity, not two to keep in step.
    assert {item.slug for item in items} == {entry.slug for entry in load_vocab().vocab}


async def test_nothing_is_active_until_it_is_reviewed(session: AsyncSession) -> None:
    """Every shipped entry is `needs_review`, so the planner can reach none of them."""
    report = await import_vocab(session)
    await session.commit()

    assert report.active == 0
    active = int((await session.execute(select(func.count()).select_from(Item).where(Item.active))).scalar_one())
    assert active == 0


async def test_include_unreviewed_activates_and_a_later_plain_import_switches_them_back(
    session: AsyncSession,
) -> None:
    """A local run with the flag must not leave production's rows switched on."""
    await import_vocab(session, include_unreviewed=True)
    await session.commit()
    assert int((await session.execute(select(func.count()).select_from(Item).where(Item.active))).scalar_one()) == (
        EXPECTED
    )

    await import_vocab(session)
    await session.commit()
    assert int((await session.execute(select(func.count()).select_from(Item).where(Item.active))).scalar_one()) == 0


async def test_an_approved_entry_becomes_active_and_a_rejected_one_never_does(
    session: AsyncSession, tmp_path: Path
) -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["vocab"][0]["gloss_review_status"] = "approved"
        data["vocab"][1]["gloss_review_status"] = "rejected"

    seed_dir = _seed_copy(tmp_path, mutate)
    report = await import_vocab(session, seed_dir=seed_dir)
    await session.commit()

    assert report.active == 1
    seed = load_vocab(seed_dir=seed_dir)
    approved_slug, rejected_slug = seed.vocab[0].slug, seed.vocab[1].slug
    by_slug = {item.slug: item.active for item in await session.scalars(select(Item))}
    assert by_slug[approved_slug] is True
    assert by_slug[rejected_slug] is False

    # --include-unreviewed activates a rejected entry too. That is what the flag means, and it is
    # exactly why deploy.sh never passes it — pinning the behaviour here so nobody "fixes" it into
    # a half-gate that looks safe in production and is not.
    await import_vocab(session, seed_dir=seed_dir, include_unreviewed=True)
    await session.commit()
    by_slug = {item.slug: item.active for item in await session.scalars(select(Item))}
    assert by_slug[rejected_slug] is True


async def test_a_rejected_entry_is_still_imported(session: AsyncSession, tmp_path: Path) -> None:
    """Rejected is a review verdict, not a delete: the row stays so the verdict is auditable."""

    def mutate(data: dict[str, Any]) -> None:
        data["vocab"][1]["gloss_review_status"] = "rejected"

    seed_dir = _seed_copy(tmp_path, mutate)
    await import_vocab(session, seed_dir=seed_dir)
    await session.commit()

    assert await _count(session, Vocab) == EXPECTED
    rejected = load_vocab(seed_dir=seed_dir).vocab[1].slug
    row = await session.scalar(select(Vocab).where(Vocab.slug == rejected))
    assert row is not None
    assert row.gloss_review_status == "rejected"


async def test_import_is_idempotent(session: AsyncSession) -> None:
    await import_vocab(session)
    await session.commit()
    first_ids = {row.slug: row.id for row in await session.scalars(select(Vocab))}

    await import_vocab(session)
    await session.commit()

    assert await _count(session, Vocab) == EXPECTED
    assert await _count(session, Item) == EXPECTED
    # Same rows, not replacements: a new id would orphan every card built on that word.
    assert {row.slug: row.id for row in await session.scalars(select(Vocab))} == first_ids


async def test_import_refreshes_an_edited_gloss_in_place(session: AsyncSession) -> None:
    await import_vocab(session)
    await session.commit()

    target = await session.scalar(select(Vocab).where(Vocab.slug == "vocab:mizu"))
    assert target is not None
    original_id, original_gloss = target.id, target.gloss_ru
    target.gloss_ru = "затёрто"
    await session.commit()

    await import_vocab(session)
    await session.commit()
    await session.refresh(target)
    assert target.id == original_id
    assert target.gloss_ru == original_gloss


async def test_import_does_not_clobber_the_enrichment_columns(session: AsyncSession) -> None:
    """`build_vocab` fills these later; a re-import of the hand-authored seed must leave them alone."""
    await import_vocab(session)
    await session.commit()

    target = await session.scalar(select(Vocab).where(Vocab.slug == "vocab:mizu"))
    assert target is not None
    target.jmdict_seq = 1217290
    target.freq_rank = 412
    await session.commit()

    await import_vocab(session)
    await session.commit()
    await session.refresh(target)
    assert target.jmdict_seq == 1217290
    assert target.freq_rank == 412


async def test_export_after_import_reproduces_the_seed_file(session: AsyncSession) -> None:
    """Rule 4: an empty `git diff` after export is the audit that the database and repo agree."""
    await import_vocab(session)
    await session.commit()

    exported = await export_vocab(session)
    on_disk = (SEED_DIR / SEED_FILE).read_text(encoding="utf-8")
    assert exported == on_disk


async def test_export_carries_a_gloss_edit_out_of_the_database(session: AsyncSession) -> None:
    """The other half of the audit: real drift must show up in the diff, not be smoothed over."""
    await import_vocab(session)
    await session.commit()

    target = await session.scalar(select(Vocab).where(Vocab.slug == "vocab:mizu"))
    assert target is not None
    target.gloss_ru = "вода (отредактировано в базе)"
    await session.commit()

    exported = await export_vocab(session)
    assert "вода (отредактировано в базе)" in exported
    assert exported != (SEED_DIR / SEED_FILE).read_text(encoding="utf-8")
