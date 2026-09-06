"""Kana grid and per-syllable progress for the Mini App.

The grid is the learner's map of the syllabary: which characters they have met, which are still
ahead, and how solid each one is. "Solid" is FSRS retrievability rather than a raw review count —
a syllable reviewed ten times a month ago is less known than one reviewed twice yesterday.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.content import Item, ItemType, Kana, KanaKind, KanaScript
from app.db.models.learning import Card, CardState
from app.domain import srs
from app.services import card_service


@dataclass(frozen=True, slots=True)
class KanaProgress:
    """One cell of the grid."""

    kana_id: uuid.UUID
    item_id: uuid.UUID
    char: str
    script: KanaScript
    cyrillic: str
    row: str
    kind: KanaKind
    group_order: int
    mnemonic_ru: str | None
    example_word: str | None
    example_reading: str | None
    example_gloss_ru: str | None
    introduced: bool
    state: CardState | None
    retrievability: float | None
    due: dt.datetime | None
    reps: int


async def kana_grid(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    now: dt.datetime,
    scripts: Sequence[KanaScript] | None = None,
    desired_retention: float = srs.DEFAULT_DESIRED_RETENTION,
) -> list[KanaProgress]:
    """Every kana in curriculum order, annotated with this learner's recognition progress."""
    stmt = (
        select(Kana, Item, Card)
        .join(Item, (Item.ref_id == Kana.id) & (Item.type == ItemType.kana))
        .outerjoin(
            Card,
            (Card.item_id == Item.id) & (Card.user_id == user_id) & (Card.direction == "recognition"),
        )
        .order_by(Item.curriculum_order)
    )
    if scripts:
        stmt = stmt.where(Kana.script.in_(scripts))

    scheduler = srs.make_scheduler(desired_retention=desired_retention, enable_fuzzing=False)
    out: list[KanaProgress] = []
    for kana, item, card in (await session.execute(stmt)).all():
        state = card_service.state_of(card) if card is not None else None
        out.append(
            KanaProgress(
                kana_id=kana.id,
                item_id=item.id,
                char=kana.char,
                script=kana.script,
                cyrillic=kana.cyrillic,
                row=kana.row,
                kind=kana.kind,
                group_order=kana.group_order,
                mnemonic_ru=kana.mnemonic_ru,
                example_word=kana.example_word,
                example_reading=kana.example_reading,
                example_gloss_ru=kana.example_gloss_ru,
                introduced=card is not None,
                state=card.state if card is not None else None,
                retrievability=(srs.retrievability(state, now, scheduler=scheduler) if state else None),
                due=card.due if card is not None else None,
                reps=card.reps if card is not None else 0,
            )
        )
    return out
