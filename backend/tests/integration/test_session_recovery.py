"""Syllables must not be lost when a sitting is abandoned or straddles midnight.

``introduce_item`` creates a card the moment the planner decides to introduce something, and from
then on the item looks introduced: ``next_new_items`` and ``remaining_new_count`` skip any item that
has a card, while the due queries skip cards still in ``new``. So an item whose session never
finished is in a hole — never offered again, never due, and counted as learned. These tests pin the
two ways a learner can fall into it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content_pipeline.import_kana import import_kana
from app.db.models.learning import Card, LearningSession, SessionStep, StepKind
from app.db.models.users import User, UserRole
from app.services import card_service, session_service, user_service
from app.services.session_service import KANA_STAGES
from app.services.user_service import TelegramIdentity
from tests.conftest import fresh_tg_id

pytestmark = pytest.mark.integration

TIMEZONE = "Europe/Berlin"


async def _learner(sessionmaker: async_sessionmaker[AsyncSession], *, now: dt.datetime) -> User:
    async with sessionmaker() as s:
        await import_kana(s)
        user = await user_service.create_user(
            s,
            TelegramIdentity(tg_user_id=fresh_tg_id(), first_name="Аня"),
            role=UserRole.learner,
            invited_by=None,
            daily_budget_usd=0.35,
            now=now,
        )
        user.timezone = TIMEZONE
        await s.commit()
        return user


async def _introduced_chars(sessionmaker: async_sessionmaker[AsyncSession], learning: LearningSession) -> set[str]:
    """The syllables a sitting actually puts in front of the learner."""
    async with sessionmaker() as s:
        steps = await s.scalars(
            select(SessionStep).where(SessionStep.session_id == learning.id, SessionStep.kind == StepKind.intro_item)
        )
        return {str(step.payload["char"]) for step in steps}


async def test_an_abandoned_lesson_offers_its_syllables_again_the_same_day(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Stopping is "not now", not "throw these five away"."""
    now = dt.datetime(2026, 4, 1, 9, 0, tzinfo=dt.UTC)
    user = await _learner(sessionmaker, now=now)

    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        before_remaining = await card_service.remaining_new_count(s, user_id=user.id, stages=KANA_STAGES)
        first, _ = await session_service.start_or_resume(s, user=learner, now=now)
        await session_service.finish(s, user=learner, learning=first, now=now, abandoned=True)
        await s.commit()

    abandoned_chars = await _introduced_chars(sessionmaker, first)
    assert abandoned_chars, "the lesson must have introduced something to lose"

    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        second, _ = await session_service.start_or_resume(s, user=learner, now=now + dt.timedelta(hours=2))
        await s.commit()

    assert await _introduced_chars(sessionmaker, second) == abandoned_chars

    async with sessionmaker() as s:
        after_remaining = await card_service.remaining_new_count(s, user_id=user.id, stages=KANA_STAGES)
    assert after_remaining == before_remaining - len(
        abandoned_chars
    ), "only the syllables actually being taught may leave the queue"


async def test_a_lesson_left_open_over_midnight_does_not_swallow_its_syllables(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The learner opens the lesson late, never answers, and comes back tomorrow.

    Yesterday's sitting is not resumable — it belongs to another local date — so tomorrow's lesson
    has to notice that those syllables were never really taught and offer them again.
    """
    # 23:30 local on 1 April in Berlin.
    late = dt.datetime(2026, 4, 1, 21, 30, tzinfo=dt.UTC)
    user = await _learner(sessionmaker, now=late)

    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        yesterday, _ = await session_service.start_or_resume(s, user=learner, now=late)
        await s.commit()
    stranded = await _introduced_chars(sessionmaker, yesterday)
    assert stranded

    # 09:00 local the next day; the earlier sitting is still in_progress but on 1 April.
    next_morning = dt.datetime(2026, 4, 2, 7, 0, tzinfo=dt.UTC)
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        today, created = await session_service.start_or_resume(s, user=learner, now=next_morning)
        await s.commit()

    assert created and today.id != yesterday.id
    assert today.local_date != yesterday.local_date
    assert (
        await _introduced_chars(sessionmaker, today) == stranded
    ), "syllables nobody ever answered must come back, not vanish into new-state cards"


async def test_an_ungraded_card_never_disappears_from_the_curriculum(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The invariant behind both cases, stated directly.

    A card in ``new`` with no review log is a promise the app made and did not keep: the learner was
    never actually asked about it. Such an item must still count as available to teach.
    """
    now = dt.datetime(2026, 4, 1, 9, 0, tzinfo=dt.UTC)
    user = await _learner(sessionmaker, now=now)

    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        total = await card_service.remaining_new_count(s, user_id=user.id, stages=KANA_STAGES)
        learning, _ = await session_service.start_or_resume(s, user=learner, now=now)
        await session_service.finish(s, user=learner, learning=learning, now=now, abandoned=True)
        await s.commit()

        cards = list(await s.scalars(select(Card).where(Card.user_id == user.id)))
        assert cards, "cards were created for the abandoned lesson"
        remaining = await card_service.remaining_new_count(s, user_id=user.id, stages=KANA_STAGES)
        offered = await card_service.next_new_items(s, user_id=user.id, stages=KANA_STAGES, limit=5)

    assert remaining == total, "nothing was taught, so nothing should have left the queue"
    assert offered, "the untaught items must still be offerable"
