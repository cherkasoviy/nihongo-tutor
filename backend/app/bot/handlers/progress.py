"""``/stats``, ``/settings``, ``/pause`` and ``/resume``.

Read-mostly commands. Settings are shown here but changed in the Mini App, where a timezone picker
and a time picker are honest UI rather than a chat prompt that has to parse free text.
"""

from __future__ import annotations

import contextlib
import datetime as dt

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import texts_ru
from app.bot.callbacks import SetReminder
from app.bot.handlers.start import identity_from_message
from app.bot.keyboards import open_app_keyboard, reminder_keyboard
from app.config import Settings
from app.db.models.users import FuriganaMode, User, UserStatus
from app.services import stats_service, user_service

router = Router(name="progress")

_FURIGANA_RU = {
    FuriganaMode.always: "всегда",
    FuriganaMode.auto: "авто",
    FuriganaMode.off: "выключена",
}


async def _require_user(message: Message, session: AsyncSession) -> User | None:
    identity = identity_from_message(message)
    if identity is None:
        return None
    user = await user_service.get_by_tg_id(session, identity.tg_user_id)
    if user is None:
        await message.answer(texts_ru.NOT_REGISTERED)
        return None
    return user


@router.message(Command("stats"))
async def cmd_stats(message: Message, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        user = await _require_user(message, session)
        if user is None:
            return
        stats = await stats_service.learner_stats(session, user_id=user.id, now=dt.datetime.now(dt.UTC))

    retention = (
        f"{round(stats.retention_7d * 100)}%" if stats.retention_7d is not None else texts_ru.STATS_RETENTION_UNKNOWN
    )
    freezes = texts_ru.STATS_FREEZES.format(n=stats.freezes_available) if stats.freezes_available else ""
    await message.answer(
        texts_ru.STATS.format(
            known=stats.kana_known,
            total=stats.kana_total,
            introduced=stats.kana_introduced,
            due=stats.due_now,
            reviews_7d=stats.reviews_7d,
            retention=retention,
            sessions=stats.sessions_completed,
            minutes_7d=stats.minutes_7d,
            streak=stats.streak_current,
            longest=stats.streak_longest,
            freezes=freezes,
        )
    )


@router.message(Command("settings"))
async def cmd_settings(message: Message, settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        user = await _require_user(message, session)
        if user is None:
            return
        reminder = user.reminder_time.strftime("%H:%M") if user.reminder_time else texts_ru.SETTINGS_NO_REMINDER
        text = texts_ru.SETTINGS.format(
            timezone=user.timezone,
            reminder=reminder,
            minutes=user.daily_minutes_target,
            furigana=_FURIGANA_RU[user.furigana_mode],
        )
    await message.answer(text, reply_markup=open_app_keyboard(settings.miniapp_url))


@router.message(Command("pause"))
async def cmd_pause(message: Message, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        user = await _require_user(message, session)
        if user is None:
            return
        if user.status == UserStatus.paused:
            await message.answer(texts_ru.ALREADY_PAUSED)
            return
        user.status = UserStatus.paused
        await session.commit()
    await message.answer(texts_ru.PAUSED)


@router.message(Command("resume"))
async def cmd_resume(message: Message, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        user = await _require_user(message, session)
        if user is None:
            return
        if user.status != UserStatus.paused:
            await message.answer(texts_ru.ALREADY_ACTIVE)
            return
        user.status = UserStatus.active
        await session.commit()
    await message.answer(texts_ru.RESUMED)


@router.callback_query(SetReminder.filter())
async def on_set_reminder(
    query: CallbackQuery,
    callback_data: SetReminder,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Record the chosen reminder hour (or switch reminders off)."""
    async with sessionmaker() as session:
        user = await user_service.get_by_tg_id(session, query.from_user.id)
        if user is None:
            await query.answer(texts_ru.NOT_REGISTERED)
            return
        user.reminder_time = None if callback_data.hour < 0 else dt.time(hour=callback_data.hour)
        await session.commit()
        chosen = user.reminder_time

    await query.answer()
    if isinstance(query.message, Message):
        text = texts_ru.REMINDER_OFF if chosen is None else texts_ru.REMINDER_SET.format(time=chosen.strftime("%H:%M"))
        with contextlib.suppress(TelegramBadRequest):
            await query.message.edit_text(text, reply_markup=None)


@router.message(Command("reminder"))
async def cmd_reminder(message: Message, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """Change the reminder time without going through the whole settings screen."""
    async with sessionmaker() as session:
        user = await _require_user(message, session)
        if user is None:
            return
        current = user.reminder_time.strftime("%H:%M") if user.reminder_time else texts_ru.SETTINGS_NO_REMINDER
    await message.answer(texts_ru.SETTINGS_ASK_REMINDER.format(current=current), reply_markup=reminder_keyboard())
