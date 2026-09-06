"""A lesson driven through the real Dispatcher: callback idempotency and in-place edits.

The plan's Phase 1 verification asks for exactly these two properties, and both are about the same
hazard. Telegram redelivers callback queries on a flaky connection, and a learner who does not see
an instant reaction taps again — so the same answer arrives twice, and it must be graded once.
"""

from __future__ import annotations

import pytest
from aiogram import Dispatcher
from aiogram.methods import EditMessageText, SendMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import texts_ru
from app.bot.callbacks import StepAck, StepChoice
from app.content_pipeline.import_kana import import_kana
from app.db.models.learning import LearningSession, ReviewLog, SessionStep, StepStatus
from app.db.models.users import User, UserRole
from app.services import user_service
from app.services.user_service import TelegramIdentity
from tests.conftest import fresh_tg_id
from tests.helpers.mocked_bot import MockedBot
from tests.helpers.updates import callback_update, command_update

pytestmark = pytest.mark.integration


async def _learner(sessionmaker: async_sessionmaker[AsyncSession]) -> tuple[int, User]:
    """A registered learner with the kana curriculum imported."""
    tg_id = fresh_tg_id()
    async with sessionmaker() as s:
        await import_kana(s)
        import datetime as dt

        user = await user_service.create_user(
            s,
            TelegramIdentity(tg_user_id=tg_id, username="anya", first_name="Аня"),
            role=UserRole.learner,
            invited_by=None,
            daily_budget_usd=0.35,
            now=dt.datetime.now(dt.UTC),
        )
        await s.commit()
        return tg_id, user


async def _first_open_step(sessionmaker: async_sessionmaker[AsyncSession], user_id: object) -> SessionStep | None:
    async with sessionmaker() as s:
        return (
            await s.scalars(
                select(SessionStep)
                .join(LearningSession, LearningSession.id == SessionStep.session_id)
                .where(LearningSession.user_id == user_id, SessionStep.status != StepStatus.answered)
                .order_by(SessionStep.idx)
                .limit(1)
            )
        ).first()


def _answer_for(step: SessionStep) -> str:
    """The callback a learner who knows the answer would send."""
    if step.payload.get("mode") == "ack":
        return StepAck(step_id=step.id).pack()
    return StepChoice(step_id=step.id, choice=int(step.payload["correct"])).pack()


async def _counts(sessionmaker: async_sessionmaker[AsyncSession], user_id: object) -> tuple[int, int]:
    """(completed steps recorded on the session, review_log rows for this learner)."""
    async with sessionmaker() as s:
        completed = (
            await s.execute(
                select(func.coalesce(func.sum(LearningSession.completed_steps), 0)).where(
                    LearningSession.user_id == user_id
                )
            )
        ).scalar_one()
        logs = (
            await s.execute(
                select(func.count())
                .select_from(ReviewLog)
                .join(SessionStep, SessionStep.card_id == ReviewLog.card_id)
                .join(LearningSession, LearningSession.id == SessionStep.session_id)
                .where(LearningSession.user_id == user_id)
            )
        ).scalar_one()
        return int(completed), int(logs)


async def test_today_builds_a_session_and_shows_the_first_step(
    dp: Dispatcher, bot: MockedBot, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tg_id, user = await _learner(sessionmaker)
    await dp.feed_update(bot, command_update(tg_id, "/today"))

    texts = bot.sent_texts()
    assert any(t.startswith("Занятие на сегодня") for t in texts), texts
    step = await _first_open_step(sessionmaker, user.id)
    assert step is not None
    assert step.status == StepStatus.shown
    assert step.tg_message_id is not None  # the message it lives in, for the in-place edit


async def test_answering_a_step_edits_it_in_place_and_drops_the_keyboard(
    dp: Dispatcher, bot: MockedBot, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tg_id, user = await _learner(sessionmaker)
    await dp.feed_update(bot, command_update(tg_id, "/today"))
    step = await _first_open_step(sessionmaker, user.id)
    assert step is not None
    await dp.feed_update(bot, callback_update(tg_id, _answer_for(step)))

    edits = [m for m in bot.mocked.requests if isinstance(m, EditMessageText)]
    assert edits, "the answered step must be rewritten, not left with a live keyboard"
    assert edits[-1].reply_markup is None


async def test_a_repeated_callback_is_graded_exactly_once(
    dp: Dispatcher, bot: MockedBot, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The property that matters: Telegram redelivers, and the learner double-taps."""
    tg_id, user = await _learner(sessionmaker)
    await dp.feed_update(bot, command_update(tg_id, "/today"))

    step = await _first_open_step(sessionmaker, user.id)
    while step is not None and step.payload.get("mode") == "ack":  # skip to a graded step
        await dp.feed_update(bot, callback_update(tg_id, StepAck(step_id=step.id).pack()))
        step = await _first_open_step(sessionmaker, user.id)
    assert step is not None

    data = StepChoice(step_id=step.id, choice=int(step.payload["correct"])).pack()
    await dp.feed_update(bot, callback_update(tg_id, data))
    after_first = await _counts(sessionmaker, user.id)

    for _ in range(3):
        await dp.feed_update(bot, callback_update(tg_id, data))

    assert await _counts(sessionmaker, user.id) == after_first
    async with sessionmaker() as s:
        refreshed = await s.get(SessionStep, step.id)
        assert refreshed is not None and refreshed.status == StepStatus.answered


async def test_a_repeated_tap_tells_the_learner_the_step_is_gone(
    dp: Dispatcher, bot: MockedBot, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tg_id, user = await _learner(sessionmaker)
    await dp.feed_update(bot, command_update(tg_id, "/today"))
    step = await _first_open_step(sessionmaker, user.id)
    assert step is not None
    data = _answer_for(step)
    await dp.feed_update(bot, callback_update(tg_id, data))
    bot.mocked.requests.clear()

    await dp.feed_update(bot, callback_update(tg_id, data))
    answers = [m for m in bot.mocked.requests if m.__api_method__ == "answerCallbackQuery"]
    assert answers and getattr(answers[-1], "text", None) == texts_ru.SESSION_STEP_GONE


async def test_a_whole_session_can_be_played_to_the_summary(
    dp: Dispatcher, bot: MockedBot, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Answer everything correctly; the bot must reach the closing summary and count the day."""
    tg_id, user = await _learner(sessionmaker)
    await dp.feed_update(bot, command_update(tg_id, "/today"))

    for _ in range(200):  # generous bound; a kana day is ~20 steps
        step = await _first_open_step(sessionmaker, user.id)
        if step is None:
            break
        await dp.feed_update(bot, callback_update(tg_id, _answer_for(step)))

    sends = [m for m in bot.mocked.requests if isinstance(m, SendMessage)]
    assert any("Занятие закончено" in (m.text or "") for m in sends), [m.text for m in sends][-3:]

    async with sessionmaker() as s:
        learning = (await s.scalars(select(LearningSession).where(LearningSession.user_id == user.id))).one()
        assert learning.completed_steps == learning.planned_steps
