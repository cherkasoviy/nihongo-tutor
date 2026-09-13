"""/diag: read-only, admin-only, and accurate.

The duplicate-practice bug took a day to find because every question about production needed a
laptop. This is the answer to that, so its tests are about the two things that would make it
useless: leaking to a learner, and being wrong.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content_pipeline.import_kana import import_kana
from app.db.models.learning import SessionKind
from app.db.models.users import User, UserRole
from app.services import diag_service, session_service, user_service
from app.services.user_service import TelegramIdentity
from tests.conftest import fresh_tg_id

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 4, 1, 9, 0, tzinfo=dt.UTC)


async def _user(sessionmaker: async_sessionmaker[AsyncSession], role: UserRole, tz: str = "Asia/Tokyo") -> User:
    async with sessionmaker() as s:
        await import_kana(s)
        user = await user_service.create_user(
            s,
            TelegramIdentity(tg_user_id=fresh_tg_id(), first_name="Аня"),
            role=role,
            invited_by=None,
            daily_budget_usd=0.35,
            now=NOW,
        )
        user.timezone = tz
        await s.commit()
        return user


async def test_it_reports_the_sittings_that_actually_happened(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await _user(sessionmaker, UserRole.learner)
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        daily, _ = await session_service.start_or_resume(s, user=learner, now=NOW)
        await s.commit()
        planned = daily.planned_steps

    async with sessionmaker() as s:
        diag = await diag_service.collect(s, now=NOW)

    me = next(lr for lr in diag.learners if lr.tg_user_id == user.tg_user_id)
    assert me.timezone == "Asia/Tokyo"
    assert [r.kind for r in me.sessions_today] == [SessionKind.daily.value]
    assert me.sessions_today[0].planned == planned, "a wrong step count is worse than no /diag"
    assert diag.content.by_type["kana"][0] == 208


async def test_the_learner_s_own_day_decides_which_sessions_count(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """03:00 UTC on 1 April is noon in Tokyo and 20:00 on 31 March in Los Angeles. Reading "today"
    in server time would show an empty day to whichever learner is the other side of the line."""
    straddle = dt.datetime(2026, 4, 1, 3, 0, tzinfo=dt.UTC)
    tokyo = await _user(sessionmaker, UserRole.learner, tz="Asia/Tokyo")
    la = await _user(sessionmaker, UserRole.learner, tz="America/Los_Angeles")

    async with sessionmaker() as s:
        diag = await diag_service.collect(s, now=straddle)
    dates = {lr.tg_user_id: lr.local_date for lr in diag.learners}
    assert dates[tokyo.tg_user_id] == dt.date(2026, 4, 1)
    assert dates[la.tg_user_id] == dt.date(2026, 3, 31)


async def test_render_carries_no_learner_writing(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The whole point is that this is safe to read in a chat. Names and answers stay out."""
    user = await _user(sessionmaker, UserRole.learner)
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        await session_service.start_or_resume(s, user=learner, now=NOW)
        await s.commit()

    async with sessionmaker() as s:
        text = diag_service.render(await diag_service.collect(s, now=NOW))

    assert "Аня" not in text, "a learner's name reached the diagnostic output"
    assert str(user.tg_user_id) in text, "without an id the numbers belong to nobody"
    assert len(text.splitlines()) < 60, "has to be readable on a phone"
