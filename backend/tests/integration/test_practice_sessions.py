"""Extra sittings: finishing today's lesson must not mean being told to come back tomorrow.

A learner who wants to keep going should be able to. What they must *not* be able to do is get
ahead of the schedule — so an extra sitting serves reviews, never new items, and cannot earn the
day a second time. The distinction lives in ``learning_sessions.kind``.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content_pipeline.import_kana import import_kana
from app.db.models.learning import (
    Card,
    CardState,
    LearningSession,
    SessionKind,
    SessionOutcome,
    SessionStep,
    StepKind,
    Streak,
)
from app.db.models.users import User, UserRole
from app.domain.grading import SelfGrade
from app.services import session_service, user_service
from app.services.user_service import TelegramIdentity
from tests.conftest import fresh_tg_id

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 4, 1, 9, 0, tzinfo=dt.UTC)


async def _learner(sessionmaker: async_sessionmaker[AsyncSession]) -> User:
    async with sessionmaker() as s:
        await import_kana(s)
        user = await user_service.create_user(
            s,
            TelegramIdentity(tg_user_id=fresh_tg_id(), first_name="Аня"),
            role=UserRole.learner,
            invited_by=None,
            daily_budget_usd=0.35,
            now=NOW,
        )
        await s.commit()
        return user


async def _play(
    sessionmaker: async_sessionmaker[AsyncSession],
    user: User,
    learning: LearningSession,
    now: dt.datetime,
    *,
    correct: bool = True,
) -> None:
    """Answer every remaining step of a sitting."""
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        for _ in range(400):
            step = await session_service.next_step(s, learning_session_id=learning.id)
            if step is None:
                break
            await session_service.mark_shown(s, step=step, now=now, message_id=None)
            mode = step.payload.get("mode")
            if mode == "ack":
                await session_service.acknowledge(s, step=step, now=now)
            elif mode == "self":
                await session_service.reveal(s, step=step, now=now)
                await session_service.submit_self_grade(
                    s,
                    user=learner,
                    step=step,
                    grade=SelfGrade.knew if correct else SelfGrade.forgot,
                    now=now,
                )
            else:
                want = int(step.payload["correct"])
                pick = want if correct else (want + 1) % max(1, len(step.payload["choices"]))
                await session_service.submit_choice(s, user=learner, step=step, choice=pick, now=now)
        current = await s.get(LearningSession, learning.id)
        assert current is not None
        await session_service.finish(s, user=learner, learning=current, now=now)
        await s.commit()


async def test_finishing_today_offers_practice_rather_than_a_locked_door(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await _learner(sessionmaker)

    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        daily, created = await session_service.start_or_resume(s, user=learner, now=NOW)
        await s.commit()
    assert created and daily.kind is SessionKind.daily

    await _play(sessionmaker, user, daily, NOW)

    # Later the same day the learner comes back for more.
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        extra, created_extra = await session_service.start_or_resume(s, user=learner, now=NOW + dt.timedelta(hours=4))
        await s.commit()

    assert created_extra, "a second sitting must actually be created"
    assert extra.id != daily.id
    assert extra.kind is SessionKind.practice
    assert extra.local_date == daily.local_date


async def test_practice_never_introduces_new_items(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The daily dose is a pedagogical decision; enthusiasm must not be able to override it."""
    user = await _learner(sessionmaker)
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        daily, _ = await session_service.start_or_resume(s, user=learner, now=NOW)
        await s.commit()
    await _play(sessionmaker, user, daily, NOW)

    async with sessionmaker() as s:
        before = int(
            (await s.execute(select(func.count()).select_from(Card).where(Card.user_id == user.id))).scalar_one()
        )
        learner = await s.get(User, user.id)
        assert learner is not None
        extra, _ = await session_service.start_or_resume(s, user=learner, now=NOW + dt.timedelta(hours=4))
        await s.commit()

        intros = int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(SessionStep)
                    .where(SessionStep.session_id == extra.id, SessionStep.kind == StepKind.intro_item)
                )
            ).scalar_one()
        )
        after = int(
            (await s.execute(select(func.count()).select_from(Card).where(Card.user_id == user.id))).scalar_one()
        )

    assert intros == 0
    assert after == before, "practice must not create cards"


async def test_practice_cannot_earn_the_day_twice(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await _learner(sessionmaker)
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        daily, _ = await session_service.start_or_resume(s, user=learner, now=NOW)
        await s.commit()
    await _play(sessionmaker, user, daily, NOW)

    async with sessionmaker() as s:
        streak = await s.get(Streak, user.id)
        assert streak is not None and streak.current == 1
        learner = await s.get(User, user.id)
        assert learner is not None
        extra, _ = await session_service.start_or_resume(s, user=learner, now=NOW + dt.timedelta(hours=4))
        await s.commit()

    await _play(sessionmaker, user, extra, NOW + dt.timedelta(hours=4))

    async with sessionmaker() as s:
        streak = await s.get(Streak, user.id)
        assert streak is not None
        assert streak.current == 1, "the chain advances once a day, however much extra work is done"


async def test_review_before_the_lesson_is_practice_and_does_not_earn_the_day(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """`/review` is explicit, so it works any time — but it must not substitute for the lesson."""
    user = await _learner(sessionmaker)

    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        # Nothing has been introduced yet, so a first-ever /review has nothing to review.
        first, _ = await session_service.start_or_resume(s, user=learner, now=NOW, want=SessionKind.practice)
        await s.commit()

    assert first.kind is SessionKind.practice
    assert first.planned_steps == 0
    assert first.outcome is SessionOutcome.abandoned, "an empty sitting closes itself"

    async with sessionmaker() as s:
        streak = await s.get(Streak, user.id)
    assert streak is None, "no streak from a sitting with no work in it"


async def test_an_empty_practice_sitting_does_not_block_the_next_start(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """An open zero-step session would be resumed forever and lock the learner out."""
    user = await _learner(sessionmaker)
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        await session_service.start_or_resume(s, user=learner, now=NOW, want=SessionKind.practice)
        await s.commit()

    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        lesson, created = await session_service.start_or_resume(s, user=learner, now=NOW)
        await s.commit()

    assert created and lesson.kind is SessionKind.daily and lesson.planned_steps > 0


async def test_an_unfinished_sitting_is_resumed_not_replaced(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await _learner(sessionmaker)
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        first, _ = await session_service.start_or_resume(s, user=learner, now=NOW)
        await s.commit()

    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        again, created = await session_service.start_or_resume(s, user=learner, now=NOW + dt.timedelta(hours=2))
        await s.commit()

    assert not created and again.id == first.id


async def test_a_practice_drill_the_scheduler_did_not_ask_for_moves_nothing(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The whole point of the intra-session flag: cramming must not push a real review out.

    One minute after the lesson the recognition cards sit on the ten-minute learning step and their
    production siblings are due tomorrow, so nothing is due and every practice step is a top-up
    drill. Answering them all must leave every card exactly where the scheduler put it.
    """
    user = await _learner(sessionmaker)
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        daily, _ = await session_service.start_or_resume(s, user=learner, now=NOW)
        await s.commit()
    await _play(sessionmaker, user, daily, NOW)

    later = NOW + dt.timedelta(minutes=1)
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        extra, _ = await session_service.start_or_resume(s, user=learner, now=later)
        await s.commit()

        steps = list(await s.scalars(select(SessionStep).where(SessionStep.session_id == extra.id)))
        snapshot = {
            c.id: (c.due, c.stability, c.difficulty, c.reps, c.state)
            for c in await s.scalars(select(Card).where(Card.user_id == user.id))
        }

    assert steps, "the fixture must actually contain practice steps"
    assert all(st.payload.get("intra_session") for st in steps), "nothing was due, so nothing is a review"

    await _play(sessionmaker, user, extra, later)

    async with sessionmaker() as s:
        after = {
            c.id: (c.due, c.stability, c.difficulty, c.reps, c.state)
            for c in await s.scalars(select(Card).where(Card.user_id == user.id))
        }

    assert after == snapshot, "a drill the scheduler did not ask for must not move any card"


async def test_a_genuinely_due_card_in_practice_is_a_real_review(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The other half: practice is not second-class when the card really is due."""
    user = await _learner(sessionmaker)
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        daily, _ = await session_service.start_or_resume(s, user=learner, now=NOW)
        await s.commit()
    await _play(sessionmaker, user, daily, NOW)

    # Two days on, the production siblings are due; ask for practice rather than the lesson.
    later = NOW + dt.timedelta(days=2)
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        before = {c.id: c.reps for c in await s.scalars(select(Card).where(Card.user_id == user.id))}
        extra, _ = await session_service.start_or_resume(s, user=learner, now=later, want=SessionKind.practice)
        await s.commit()
        assert extra.planned_steps > 0

    await _play(sessionmaker, user, extra, later)

    async with sessionmaker() as s:
        moved = [
            c
            for c in await s.scalars(select(Card).where(Card.user_id == user.id))
            if c.reps > before.get(c.id, 0) and c.state != CardState.new
        ]
    assert moved, "a due card answered during practice must be scheduled like any other review"
