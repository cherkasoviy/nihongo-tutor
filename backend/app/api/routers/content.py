"""``/api/content``: the shared curriculum, annotated with this learner's progress."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser
from app.api.schemas import KanaCellOut
from app.db.base import SessionDep
from app.db.models.content import KanaScript
from app.services import kana_service

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
