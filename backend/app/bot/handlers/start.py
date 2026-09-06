"""``/start [code]``: invite redemption, admin bootstrap, returning learners."""

from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import texts_ru
from app.bot.keyboards import open_app_keyboard, reminder_keyboard
from app.config import Settings
from app.db.models.users import User
from app.services.onboarding_service import StartOutcome, handle_start
from app.services.user_service import TelegramIdentity

router = Router(name="start")


def identity_from_message(message: Message) -> TelegramIdentity | None:
    if message.from_user is None or message.from_user.is_bot:
        return None
    u = message.from_user
    return TelegramIdentity(
        tg_user_id=u.id, username=u.username, first_name=u.first_name, language_code=u.language_code
    )


@router.message(CommandStart(deep_link=False))
@router.message(CommandStart(deep_link=True))
async def cmd_start(
    message: Message,
    command: CommandObject,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    identity = identity_from_message(message)
    if identity is None:
        return
    code = (command.args or "").strip() or None

    async with sessionmaker() as session:
        result = await handle_start(
            session,
            identity=identity,
            code=code,
            admin_tg_ids=settings.admin_tg_ids,
            daily_budget_usd=settings.default_daily_budget_usd,
        )

    name = escape(texts_ru.display_name(identity.first_name, identity.username))
    keyboard = open_app_keyboard(settings.miniapp_url)

    match result.outcome:
        case StartOutcome.returning:
            await message.answer(texts_ru.WELCOME_BACK.format(name=name), reply_markup=keyboard)
        case StartOutcome.admin_created:
            await message.answer(texts_ru.WELCOME_ADMIN.format(name=name), reply_markup=keyboard)
            await _ask_reminder(message, result.user)
        case StartOutcome.invited:
            await message.answer(texts_ru.WELCOME_NEW.format(name=name), reply_markup=keyboard)
            await _ask_reminder(message, result.user)
        case StartOutcome.needs_code:
            await message.answer(texts_ru.NEEDS_CODE)
        case StartOutcome.invite_invalid:
            assert result.failure is not None
            await message.answer(texts_ru.INVITE_INVALID[result.failure])


async def _ask_reminder(message: Message, user: User | None) -> None:
    """Ask for a reminder time immediately after joining.

    Without this the reminder cron has nothing to select — ``reminder_time`` stays NULL for every
    learner and the daily nudge, which is the whole point of putting this in a chat app, never
    fires. Asking here rather than in the Mini App keeps onboarding to one screen.
    """
    if user is None:
        return
    await message.answer(
        texts_ru.ASK_REMINDER.format(timezone=user.timezone),
        reply_markup=reminder_keyboard(),
    )
