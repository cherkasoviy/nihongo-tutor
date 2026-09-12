"""``/api/content``: the shared curriculum, annotated with this learner's progress."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser
from app.api.schemas import KanaCellOut, PlacementIn, PlacementOut
from app.db.base import SessionDep
from app.db.models.content import KanaScript
from app.services import kana_service, placement_service

router = APIRouter(prefix="/content", tags=["content"])


@router.get("/kana", response_model=list[KanaCellOut])
async def kana_grid(
    user: CurrentUser,
    session: SessionDep,
    script: Annotated[KanaScript | None, Query()] = None,
) -> list[KanaCellOut]:
    cells = await kana_service.kana_grid(
        session,
        user_id=user.id,
        now=dt.datetime.now(dt.UTC),
        scripts=[script] if script else None,
        desired_retention=float(user.desired_retention),
    )
    return [
        KanaCellOut(
            item_id=c.item_id,
            char=c.char,
            script=c.script.value,
            cyrillic=c.cyrillic,
            row=c.row,
            kind=c.kind.value,
            group_order=c.group_order,
            mnemonic_ru=c.mnemonic_ru,
            example_word=c.example_word,
            example_reading=c.example_reading,
            example_gloss_ru=c.example_gloss_ru,
            introduced=c.introduced,
            state=c.state.value if c.state else None,
            retrievability=c.retrievability,
            due=c.due,
            reps=c.reps,
        )
        for c in cells
    ]


@router.post("/kana/known", response_model=PlacementOut)
async def set_kana_known(body: PlacementIn, user: CurrentUser, session: SessionDep) -> PlacementOut:
    """Claim syllables as already known, or take a claim back.

    Claiming does not skip: it seeds each card as though it had been answered correctly twice and
    pulls the due date into a spread window, so the scheduler checks every claim within a couple of
    weeks instead of taking it on trust. Taking a claim back only removes cards that were never
    actually tested — an answered card is real history, and "I do not know this after all" is what
    answering Не помню is for.
    """
    now = dt.datetime.now(dt.UTC)
    if body.known:
        result = await placement_service.mark_known(session, user_id=user.id, item_ids=body.item_ids, now=now)
    else:
        result = await placement_service.unmark_known(session, user_id=user.id, item_ids=body.item_ids)
    await session.commit()
    return PlacementOut(
        seeded=result.seeded,
        skipped_already_reviewed=result.skipped_already_reviewed,
        cleared=result.cleared,
    )
