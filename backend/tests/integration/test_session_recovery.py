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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content_pipeline.import_kana import import_kana
from app.db.models.learning import (
    Card,
    LearningSession,
    SessionKind,
    SessionOutcome,
    SessionStep,
    StepKind,
    Streak,
)
from app.db.models.users import User, UserRole
from app.domain.grading import SelfGrade
from app.services import card_service, session_service, user_service
from app.services.session_service import KANA_STAGES
from app.services.user_service import TelegramIdentity
from tests.conftest import fresh_tg_id

pytestmark = pytest.mark.integration

TIMEZONE = "Europe/Berlin"
NOW = dt.datetime(2026, 4, 1, 9, 0, tzinfo=dt.UTC)


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


async def _play(
    sessionmaker: async_sessionmaker[AsyncSession],
    user: User,
    learning: LearningSession,
    now: dt.datetime,
) -> None:
    """Answer every remaining step correctly and close the sitting."""
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
                await session_service.submit_self_grade(s, user=learner, step=step, grade=SelfGrade.knew, now=now)
            else:
                await session_service.submit_choice(
                    s, user=learner, step=step, choice=int(step.payload["correct"]), now=now
                )
        current = await s.get(LearningSession, learning.id)
        assert current is not None
        await session_service.finish(s, user=learner, learning=current, now=now)
        await s.commit()


async def test_an_abandoned_lesson_offers_its_syllables_again_the_same_day(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Stopping is "not now", not "throw these five away".

    Coming back the same day must hand the learner the same lesson, and must not open a second
    planned lesson for the date — the day's new-item dose is issued once.
    """
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
        dailies = list(
            await s.scalars(
                select(LearningSession).where(
                    LearningSession.user_id == user.id, LearningSession.kind == SessionKind.daily
                )
            )
        )
        mid_remaining = await card_service.remaining_new_count(s, user_id=user.id, stages=KANA_STAGES)
    assert len(dailies) == 1, "a date carries one planned lesson, not one per attempt"
    assert mid_remaining == before_remaining, "nothing has been answered yet, so nothing is learned yet"

    # Finish it properly and the syllables finally leave the queue.
    await _play(sessionmaker, user, second, now + dt.timedelta(hours=2))
    async with sessionmaker() as s:
        after_remaining = await card_service.remaining_new_count(s, user_id=user.id, stages=KANA_STAGES)
    assert after_remaining == before_remaining - len(abandoned_chars)


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


async def test_the_database_refuses_a_second_planned_lesson_for_one_day(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The invariant is enforced, not merely intended.

    The stop-button defect issued the day's new items twice, and nothing in the schema objected.
    A partial unique index now makes that unrepresentable, so the next bug in this area fails loudly
    instead of quietly consuming curriculum. Practice sittings are unlimited by design and stay
    outside the index.
    """
    now = dt.datetime(2026, 4, 1, 9, 0, tzinfo=dt.UTC)
    user = await _learner(sessionmaker, now=now)

    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        first, _ = await session_service.start_or_resume(s, user=learner, now=now)
        await s.commit()
        local_date = first.local_date

    with pytest.raises(IntegrityError):
        async with sessionmaker() as s:
            s.add(
                LearningSession(
                    user_id=user.id,
                    local_date=local_date,
                    started_at=now,
                    kind=SessionKind.daily,
                    planned_steps=0,
                )
            )
            await s.commit()

    # Two practice sittings on the same day are fine, and must stay fine.
    async with sessionmaker() as s:
        for _ in range(2):
            s.add(
                LearningSession(
                    user_id=user.id,
                    local_date=local_date,
                    started_at=now,
                    kind=SessionKind.practice,
                    planned_steps=0,
                )
            )
        await s.commit()


# --- being ahead of the curriculum -----------------------------------------------------------
# Once everything available has been taught and nothing is due, the day's session has no steps in
# it. That is the normal state for a fortnight after the kana stage finishes, not an edge case, and
# it must not cost the learner their streak.


async def _learner_with_nothing_to_do(sessionmaker: async_sessionmaker[AsyncSession]) -> User:
    """A learner who has run out of curriculum: no content, so nothing to teach and nothing due.

    The same state a learner reaches by finishing the syllabary, without simulating three weeks to
    get there — and it does not depend on where FSRS happens to place an interval.
    """
    async with sessionmaker() as s:
        user = await user_service.create_user(
            s,
            TelegramIdentity(tg_user_id=fresh_tg_id(), first_name="Аня"),
            role=UserRole.learner,
            invited_by=None,
            daily_budget_usd=0.35,
            now=NOW,
        )
        user.timezone = TIMEZONE
        await s.commit()
        return user


async def test_a_day_with_nothing_to_do_still_counts(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await _learner_with_nothing_to_do(sessionmaker)

    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        learning, _ = await session_service.start_or_resume(s, user=learner, now=NOW)
        await s.commit()
        streak = await s.get(Streak, user.id)

    assert learning.planned_steps == 0
    assert learning.outcome is SessionOutcome.completed, "showing up to an empty day is not failing it"
    assert streak is not None and streak.current == 1


async def test_a_run_of_empty_days_does_not_break_the_streak(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The case that matters: finishing the curriculum leaves weeks of these."""
    user = await _learner_with_nothing_to_do(sessionmaker)

    for day in range(12):
        async with sessionmaker() as s:
            learner = await s.get(User, user.id)
            assert learner is not None
            await session_service.start_or_resume(s, user=learner, now=NOW + dt.timedelta(days=day))
            await s.commit()

    async with sessionmaker() as s:
        streak = await s.get(Streak, user.id)
    assert streak is not None
    assert streak.current == 12, "twelve days of turning up is a twelve-day streak"
    assert streak.longest == 12
    assert not streak.freeze_used_dates, "an empty day is not a gap; no freeze should be spent"


async def test_an_empty_day_does_not_block_the_next_one(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """It is closed on creation, so it can never be resumed forever."""
    user = await _learner_with_nothing_to_do(sessionmaker)

    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        first, _ = await session_service.start_or_resume(s, user=learner, now=NOW)
        await s.commit()
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        second, created = await session_service.start_or_resume(s, user=learner, now=NOW + dt.timedelta(days=1))
        await s.commit()

    assert created and second.id != first.id


async def test_an_empty_practice_tap_still_earns_nothing(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Only the planned lesson may be carried by there being nothing to do."""
    user = await _learner_with_nothing_to_do(sessionmaker)

    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        practice, _ = await session_service.start_or_resume(s, user=learner, now=NOW, want=SessionKind.practice)
        await s.commit()
        streak = await s.get(Streak, user.id)

    assert practice.kind is SessionKind.practice
    assert practice.planned_steps == 0
    assert practice.outcome is SessionOutcome.abandoned
    assert streak is None, "tapping /review with nothing due is not a day's work"
