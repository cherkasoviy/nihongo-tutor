"""``/api/stats`` and ``/api/settings``."""

from __future__ import annotations

import dataclasses
import datetime as dt

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser
from app.api.schemas import SettingsIn, StatsOut, UserOut
from app.db.base import SessionDep
from app.db.models.users import FuriganaMode
from app.domain import clock
from app.services import stats_service

router = APIRouter(tags=["stats"])


@router.get("/stats", response_model=StatsOut)
async def get_stats(user: CurrentUser, session: SessionDep) -> StatsOut:
    stats = await stats_service.learner_stats(session, user_id=user.id, now=dt.datetime.now(dt.UTC))
    # ``asdict``, not ``vars``: LearnerStats is a slotted dataclass and has no ``__dict__``.
    return StatsOut(**dataclasses.asdict(stats))


@router.patch("/settings", response_model=UserOut)
async def update_settings(body: SettingsIn, user: CurrentUser, session: SessionDep) -> UserOut:
    if body.timezone is not None:
        # A bad IANA name would silently move every future reminder and local date to UTC.
        if clock.zone(body.timezone).key != body.timezone:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="unknown timezone")
        user.timezone = body.timezone
    if body.clear_reminder:
        user.reminder_time = None
    elif body.reminder_time is not None:
        user.reminder_time = body.reminder_time
    if body.daily_minutes_target is not None:
        user.daily_minutes_target = body.daily_minutes_target
    if body.reset_new_items_target:
        user.daily_new_items_target = None
    elif body.daily_new_items_target is not None:
        user.daily_new_items_target = body.daily_new_items_target
    if body.furigana_mode is not None:
        user.furigana_mode = FuriganaMode(body.furigana_mode)
    await session.commit()
    return UserOut.from_user(user)
