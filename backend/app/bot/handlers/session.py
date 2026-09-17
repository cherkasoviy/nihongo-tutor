"""``/today`` and ``/review``: running a lesson inside the chat.

Each step is its own message. When it is answered the message is rewritten in place — question plus
verdict, keyboard removed — and the next step arrives as a new message. That leaves a readable
transcript of the session and, more importantly, means a stale keyboard cannot be tapped twice:
the buttons are gone the moment the answer lands, and ``session_steps.status`` catches the taps that
were already in flight.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import uuid
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import render, texts_ru, voice
from app.bot.callbacks import SessionAction, StepAck, StepChoice, StepExample, StepReveal, StepSelfGrade
from app.bot.handlers.start import identity_from_message
from app.bot.keyboards import stop_keyboard
from app.config import get_settings
from app.db.models.content import Item, ItemType, Kana
from app.db.models.learning import (
    DailyPlan,
    LearningSession,
    SessionKind,
    SessionStep,
    StepKind,
    StepStatus,
    Streak,
)
from app.db.models.users import User, UserStatus
from app.domain import clock
from app.domain.grading import SelfGrade
from app.logging import get_logger
from app.services import session_service, user_service

log = get_logger(__name__)
router = Router(name="session")

# A step the learner can still act on. Anything else is history.
_OPEN_STEP = (StepStatus.pending, StepStatus.shown)


async def _resolve_user(session: AsyncSession, tg_user_id: int) -> User | None:
    return await user_service.get_by_tg_id(session, tg_user_id)


async def _send_next(message: Message, session: AsyncSession, learning: LearningSession) -> bool:
    """Send the next pending step. Returns False when the session has run out of steps."""
    step = await session_service.next_step(session, learning_session_id=learning.id)
    if step is None:
        return False
    view = render.render_step(step)

    sent: Message | None = None
    if step.kind is StepKind.intro_item:
        # The card *is* the voice message. A learner meeting a sound for the first time should hear
        # it without deciding to, and one message keeps the edit-in-place design: the caption is
        # rewritten on answer exactly as the text would have been.
        sent = await voice.send_voice(
            message,
            session,
            settings=get_settings(),
            text=str(step.payload.get("char", "")),
            caption=view.text,
            keyboard=view.keyboard,
        )
    if sent is None:  # no audio, or audio failed: the lesson carries on silently
        sent = await message.answer(view.text, reply_markup=view.keyboard)

    await session_service.mark_shown(session, step=step, now=dt.datetime.now(dt.UTC), message_id=sent.message_id)
    return True


@router.message(Command("today"))
async def cmd_today(
    message: Message,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The day's lesson — or, once that is done, an extra practice sitting."""
    await _start(message, sessionmaker, want=None)


@router.message(Command("review"))
async def cmd_review(
    message: Message,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The plan's short repetition: five minutes of reviews, never new items.

    Asked for explicitly, so it starts a practice sitting even before today's lesson is done — and
    it cannot earn the day, because that is the lesson's job.
    """
    await _start(message, sessionmaker, want=SessionKind.practice)


async def _start(
    message: Message,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    want: SessionKind | None,
) -> None:
    identity = identity_from_message(message)
    if identity is None:
        return
    now = dt.datetime.now(dt.UTC)

    async with sessionmaker() as session:
        user = await _resolve_user(session, identity.tg_user_id)
        if user is None:
            await message.answer(texts_ru.NOT_REGISTERED)
            return
        if user.status == UserStatus.paused:
            await message.answer(texts_ru.ALREADY_PAUSED)
            return

        learning, created = await session_service.start_or_resume(session, user=user, now=now, want=want)
        await session.commit()

        practice = learning.kind is SessionKind.practice
        if learning.planned_steps == 0:
            streak = await _streak_of(session, user)
            await message.answer(
                texts_ru.PRACTICE_NOTHING_DUE.format(streak=streak, streak_word=texts_ru.streak_word(streak))
                if practice
                else texts_ru.TODAY_NOTHING_DUE
            )
            return

        remaining = learning.planned_steps - learning.completed_steps
        if created and practice:
            opener = texts_ru.PRACTICE_INTRO.format(steps=learning.planned_steps)
        elif created:
            minutes = max(1, round(learning.planned_steps * 8 / 60))
            opener = texts_ru.TODAY_INTRO.format(steps=learning.planned_steps, minutes=minutes)
        else:
            template = texts_ru.PRACTICE_RESUME if practice else texts_ru.TODAY_RESUME
            opener = template.format(left=remaining, total=learning.planned_steps)
        # The way out lives on the opening message rather than on every step: a session the learner
        # cannot stop is a session they will abandon by closing the app, which looks the same to the
        # scheduler but loses the progress they had earned.
        await message.answer(opener, reply_markup=stop_keyboard())

        await _send_next(message, session, learning)
        await session.commit()


async def _streak_of(session: AsyncSession, user: User) -> int:
    row = await session.get(Streak, user.id)
    return row.current if row else 0


async def _answer_and_advance(
    query: CallbackQuery,
    session: AsyncSession,
    *,
    user: User,
    step: SessionStep,
    outcome: session_service.AnswerOutcome,
) -> None:
    """Rewrite the answered message, then send the next step or the wrap-up."""
    if not outcome.accepted:
        await query.answer(texts_ru.SESSION_STEP_GONE)
        return

    message = query.message
    if isinstance(message, Message):
        feedback = render.render_feedback(step, outcome)
        try:
            # A voice message has a caption, not text, and edit_text on one is an error. The intro
            # step is sent as voice, so which of these applies depends on the step kind.
            if message.voice is not None:
                await message.edit_caption(caption=feedback, reply_markup=None)
            else:
                await message.edit_text(feedback, reply_markup=None)
        except TelegramBadRequest:
            # Identical text, or a message too old to edit: not worth failing the answer over.
            log.debug("step edit skipped", step_id=str(step.id))

    await query.answer()

    learning = await session.get(LearningSession, step.session_id)
    if learning is None or not isinstance(message, Message):
        return
    if not await _send_next(message, session, learning):
        counted = await session_service.finish(session, user=user, learning=learning, now=dt.datetime.now(dt.UTC))
        await message.answer(await _summary(session, user=user, learning=learning, counted=counted))


async def _summary(session: AsyncSession, *, user: User, learning: LearningSession, counted: bool) -> str:
    """The closing message: what was covered, how it went, and where the streak stands."""
    graded = int(
        (
            await session.execute(
                select(func.count())
                .select_from(SessionStep)
                .where(
                    SessionStep.session_id == learning.id,
                    SessionStep.status == StepStatus.answered,
                    SessionStep.result["correct"].astext.is_not(None),
                )
            )
        ).scalar_one()
    )
    correct = int(
        (
            await session.execute(
                select(func.count())
                .select_from(SessionStep)
                .where(
                    SessionStep.session_id == learning.id,
                    SessionStep.result["correct"].astext == "true",
                )
            )
        ).scalar_one()
    )
    accuracy = round(100 * correct / graded) if graded else 100
    if learning.kind is SessionKind.practice:
        return texts_ru.PRACTICE_DONE.format(reviews=learning.completed_steps, accuracy=accuracy)

    plan = await session.get(DailyPlan, (user.id, learning.local_date))
    streak = await _streak_of(session, user) if counted else 0

    return texts_ru.SESSION_DONE.format(
        new_items=plan.new_items_target if plan else 0,
        reviews=learning.completed_steps,
        accuracy=accuracy,
        streak=streak,
        streak_word=texts_ru.streak_word(streak),
        tomorrow=texts_ru.SESSION_DONE_TOMORROW_EMPTY,
    )


def _example_caption(payload: dict[str, Any]) -> str | None:
    """What the voice message says it is.

    A bare voice bubble is anonymous: tapped twice and scrolled past, it is two identical grey
    blobs. The card prints the word next to the button; the caption is the chat's version of that.
    """
    word = payload.get("example_word")
    if not word:
        return None
    gloss = payload.get("example_gloss_ru")
    if gloss:
        return texts_ru.VOICE_EXAMPLE_CAPTION.format(word=word, gloss=gloss)
    return texts_ru.VOICE_EXAMPLE_CAPTION_BARE.format(word=word)


async def _example_reading(session: AsyncSession, *, item_id: uuid.UUID | None) -> str:
    """The kana reading of a syllable's example word, or "" when there is none."""
    if item_id is None:
        return ""
    row = (
        await session.execute(
            select(Kana).join(Item, (Item.ref_id == Kana.id) & (Item.type == ItemType.kana)).where(Item.id == item_id)
        )
    ).scalar_one_or_none()
    return (row.example_reading or "") if row is not None else ""


@router.callback_query(StepExample.filter())
async def on_example(
    query: CallbackQuery,
    callback_data: StepExample,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Play the example word for an introduction card.

    Closes the gap with the Mini App, which has had a button for this since audio landed: the chat
    learner could hear the syllable but never the word it lives in.

    Sends ``example_reading``, never ``example_word``. They coincide for kana; the rule is the point,
    because the day a vocabulary item is 日本 the written form would be synthesised にっぽん.
    """
    message = query.message
    async with sessionmaker() as session:
        user = await _resolve_user(session, query.from_user.id)
        step = await session.get(SessionStep, callback_data.step_id)
        if user is None or step is None or not isinstance(message, Message):
            await query.answer(texts_ru.SESSION_STEP_GONE)
            return

        learning = await session.get(LearningSession, step.session_id)
        if learning is None or learning.user_id != user.id or step.status not in _OPEN_STEP:
            await query.answer(texts_ru.SESSION_STEP_GONE)
            return

        # Resolved from the Kana row, not the step payload. The payload carries the example *word*
        # for display and never carried its reading — and steps already sitting in production would
        # not gain one, so the row is the only source that works for a lesson already in flight.
        reading = await _example_reading(session, item_id=step.item_id)
        sent = (
            await voice.send_voice(
                message,
                session,
                settings=get_settings(),
                text=reading,
                caption=_example_caption(step.payload),
                # Quotes the card, so a sound in yesterday's history still points at its syllable.
                reply=True,
            )
            if reading
            else None
        )
        await session.commit()

    # A missing clip is a toast, not an error: the card and its buttons are untouched either way.
    await query.answer() if sent is not None else await query.answer(texts_ru.AUDIO_UNAVAILABLE)


@router.callback_query(StepChoice.filter())
async def on_choice(
    query: CallbackQuery,
    callback_data: StepChoice,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user = await _resolve_user(session, query.from_user.id)
        step = await session.get(SessionStep, callback_data.step_id)
        if user is None or step is None:
            await query.answer(texts_ru.SESSION_STEP_GONE)
            return
        outcome = await session_service.submit_choice(
            session, user=user, step=step, choice=callback_data.choice, now=dt.datetime.now(dt.UTC)
        )
        await _answer_and_advance(query, session, user=user, step=step, outcome=outcome)
        await session.commit()


@router.callback_query(StepAck.filter())
async def on_ack(
    query: CallbackQuery,
    callback_data: StepAck,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user = await _resolve_user(session, query.from_user.id)
        step = await session.get(SessionStep, callback_data.step_id)
        if user is None or step is None:
            await query.answer(texts_ru.SESSION_STEP_GONE)
            return
        outcome = await session_service.acknowledge(session, step=step, now=dt.datetime.now(dt.UTC))
        await _answer_and_advance(query, session, user=user, step=step, outcome=outcome)
        await session.commit()


@router.callback_query(StepSelfGrade.filter())
async def on_self_grade(
    query: CallbackQuery,
    callback_data: StepSelfGrade,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user = await _resolve_user(session, query.from_user.id)
        step = await session.get(SessionStep, callback_data.step_id)
        if user is None or step is None:
            await query.answer(texts_ru.SESSION_STEP_GONE)
            return
        outcome = await session_service.submit_self_grade(
            session, user=user, step=step, grade=SelfGrade(callback_data.grade), now=dt.datetime.now(dt.UTC)
        )
        await _answer_and_advance(query, session, user=user, step=step, outcome=outcome)
        await session.commit()


@router.callback_query(SessionAction.filter(F.action == "stop"))
async def on_stop(
    query: CallbackQuery,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        user = await _resolve_user(session, query.from_user.id)
        if user is None:
            await query.answer()
            return
        now = dt.datetime.now(dt.UTC)
        learning = await session_service.in_progress_session(
            session, user_id=user.id, local_date=clock.local_date(now, user.timezone)
        )
        if learning is None:
            # Nothing running: an old keyboard, or a second tap. Starting a lesson here would
            # introduce the next batch of syllables and bin them in the same breath.
            await query.answer(texts_ru.SESSION_ALREADY_STOPPED)
            return
        await session_service.finish(session, user=user, learning=learning, now=now, abandoned=True)
        await session.commit()
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.answer(texts_ru.SESSION_STOPPED)


@router.callback_query(StepReveal.filter())
async def on_reveal(
    query: CallbackQuery,
    callback_data: StepReveal,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Show the answer on a self-graded step, then offer the three grades.

    The step stays open: revealing is not answering. The time taken to get here is what decides
    Hard, so it is recorded now rather than when the learner finally picks a button.
    """
    async with sessionmaker() as session:
        step = await session.get(SessionStep, callback_data.step_id)
        if step is None or step.status not in (StepStatus.pending, StepStatus.shown):
            await query.answer(texts_ru.SESSION_STEP_GONE)
            return
        await session_service.reveal(session, step=step, now=dt.datetime.now(dt.UTC))
        await session.commit()
        view = render.render_revealed(step)

    await query.answer()
    if isinstance(query.message, Message):
        with contextlib.suppress(TelegramBadRequest):
            await query.message.edit_text(view.text, reply_markup=view.keyboard)
