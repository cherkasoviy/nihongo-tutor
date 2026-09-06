"""The kana seed import: correctness of the shipped data, and idempotency of the importer.

The plan requires the content pipeline to be re-runnable ("idempotent upserts"), because it is run
again on every deploy that changes seed content. Importing twice must refresh rows, never duplicate
them or renumber the curriculum.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content_pipeline.import_kana import import_kana, item_slug
from app.content_pipeline.kana_seed import load_all
from app.db.models.content import Item, ItemStage, ItemType, Kana, KanaKind, KanaScript

pytestmark = pytest.mark.integration

EXPECTED_PER_SCRIPT = 104  # 46 basic + 20 dakuten + 5 handakuten + 33 yoon


async def _count(session: AsyncSession, model: type[Kana] | type[Item]) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


async def test_import_creates_every_kana_and_its_item(session: AsyncSession) -> None:
    report = await import_kana(session)
    await session.commit()

    assert report.kana_seen == EXPECTED_PER_SCRIPT * 2
    assert await _count(session, Kana) == EXPECTED_PER_SCRIPT * 2
    assert await _count(session, Item) == EXPECTED_PER_SCRIPT * 2

    for script, stage in ((KanaScript.hiragana, ItemStage.kana_hira), (KanaScript.katakana, ItemStage.kana_kata)):
        rows = list(await session.scalars(select(Kana).where(Kana.script == script)))
        assert len(rows) == EXPECTED_PER_SCRIPT
        kinds = {k: sum(1 for r in rows if r.kind is k) for k in KanaKind}
        assert kinds == {KanaKind.basic: 46, KanaKind.dakuten: 20, KanaKind.handakuten: 5, KanaKind.yoon: 33}

        staged = int(
            (
                await session.execute(
                    select(func.count()).select_from(Item).where(Item.stage == stage, Item.type == ItemType.kana)
                )
            ).scalar_one()
        )
        assert staged == EXPECTED_PER_SCRIPT


async def test_hiragana_sorts_before_katakana_in_the_curriculum(session: AsyncSession) -> None:
    """The whole curriculum is ordered by one integer; the scripts must not interleave."""
    await import_kana(session)
    await session.commit()

    orders = (
        await session.execute(
            select(Kana.script, func.min(Item.curriculum_order), func.max(Item.curriculum_order))
            .join(Item, (Item.ref_id == Kana.id) & (Item.type == ItemType.kana))
            .group_by(Kana.script)
        )
    ).all()
    by_script = {script: (lo, hi) for script, lo, hi in orders}
    assert by_script[KanaScript.hiragana][1] < by_script[KanaScript.katakana][0]


async def test_import_is_idempotent(session: AsyncSession) -> None:
    await import_kana(session)
    await session.commit()
    first_ids = {row.char: row.id for row in await session.scalars(select(Kana))}

    await import_kana(session)
    await session.commit()

    assert await _count(session, Kana) == EXPECTED_PER_SCRIPT * 2
    assert await _count(session, Item) == EXPECTED_PER_SCRIPT * 2
    # Same rows, not replacements: a new id would orphan every learner's cards.
    assert {row.char: row.id for row in await session.scalars(select(Kana))} == first_ids


async def test_import_refreshes_edited_content(session: AsyncSession) -> None:
    """Re-running after a seed edit updates the row in place, keeping its id and its cards."""
    await import_kana(session)
    await session.commit()

    target = await session.scalar(select(Kana).where(Kana.char == "あ"))
    assert target is not None
    original_id, original_mnemonic = target.id, target.mnemonic_ru
    target.mnemonic_ru = "затёрто"
    await session.commit()

    await import_kana(session)
    await session.commit()
    await session.refresh(target)
    assert target.id == original_id
    assert target.mnemonic_ru == original_mnemonic


async def test_item_slugs_are_unique_and_stable(session: AsyncSession) -> None:
    """Slugs key the curriculum, so a collision would silently drop a character.

    ``romaji_key`` alone is not enough: modern Japanese merged ぢ into じ and づ into ず, so each
    script ships two syllables keyed ``ji`` and two keyed ``zu``.
    """
    await import_kana(session)
    await session.commit()

    slugs = [row for row in await session.scalars(select(Item.slug))]
    assert len(set(slugs)) == len(slugs)

    seed = load_all()
    expected = {item_slug(f.script, entry.row, entry.romaji_key) for f in seed for entry in f.kana}
    assert set(slugs) == expected
