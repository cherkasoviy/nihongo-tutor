"""``/start`` and ``/admin`` through the real Dispatcher with a network-less bot."""

import pytest
from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import texts_ru
from app.services import invite_service, user_service
from tests.conftest import ADMIN_TG_ID, fresh_tg_id
from tests.helpers.mocked_bot import MockedBot
from tests.helpers.updates import command_update

pytestmark = pytest.mark.integration


async def _make_invite(sessionmaker: async_sessionmaker[AsyncSession], max_uses: int = 1) -> str:
    async with sessionmaker() as s:
        invite = await invite_service.create_invite(s, created_by=None, max_uses=max_uses)
        await s.commit()
        return invite.code


async def test_start_without_code_asks_for_invite(dp: Dispatcher, bot: MockedBot) -> None:
    await dp.feed_update(bot, command_update(fresh_tg_id(), "/start"))
    assert bot.sent_texts() == [texts_ru.NEEDS_CODE]


async def test_start_with_bad_code(dp: Dispatcher, bot: MockedBot) -> None:
    await dp.feed_update(bot, command_update(fresh_tg_id(), "/start NOPE1234"))
    assert bot.sent_texts() == [texts_ru.INVITE_INVALID[invite_service.InviteFailure.not_found]]


async def test_start_with_invite_registers_learner(
    dp: Dispatcher, bot: MockedBot, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    code = await _make_invite(sessionmaker)
    tg_id = fresh_tg_id()
    await dp.feed_update(bot, command_update(tg_id, f"/start {code}", first_name="Аня"))
    texts = bot.sent_texts()
    # Welcome, then the reminder question: without an answer to that the cron has nothing to
    # select and the daily nudge — the whole reason this lives in a chat app — never fires.
    assert len(texts) == 2
    assert texts[0].startswith("Привет, Аня!")
    assert texts[1].startswith("Когда напоминать")
    # The Mini App button rides along because MINIAPP_URL is https in tests.
    request = bot.mocked.get_request()
    assert request.reply_markup is not None  # type: ignore[attr-defined]

    async with sessionmaker() as s:
        user = await user_service.get_by_tg_id(s, tg_id)
        assert user is not None and not user.is_admin

    # Second /start is a returning user, and the same code cannot be reused by someone else.
    await dp.feed_update(bot, command_update(tg_id, "/start"))
    assert bot.sent_texts()[-1] == texts_ru.WELCOME_BACK.format(name="Аня")
    await dp.feed_update(bot, command_update(fresh_tg_id(), f"/start {code}"))
    assert bot.sent_texts()[-1] == texts_ru.INVITE_INVALID[invite_service.InviteFailure.exhausted]


async def test_admin_bootstrap_from_env(
    dp: Dispatcher, bot: MockedBot, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await dp.feed_update(bot, command_update(ADMIN_TG_ID, "/start", first_name="Игорь", username="igor"))
    assert bot.sent_texts()[0] == texts_ru.WELCOME_ADMIN.format(name="Игорь")
    assert bot.sent_texts()[1].startswith("Когда напоминать")
    async with sessionmaker() as s:
        user = await user_service.get_by_tg_id(s, ADMIN_TG_ID)
        assert user is not None and user.is_admin


async def test_admin_invite_command(
    dp: Dispatcher, bot: MockedBot, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await dp.feed_update(bot, command_update(ADMIN_TG_ID, "/start"))
    await dp.feed_update(bot, command_update(ADMIN_TG_ID, "/admin invite 3 7"))
    text = bot.sent_texts()[-1]
    assert text.startswith("Приглашение создано.")
    assert "Использований: 3" in text
    assert "https://t.me/nihongo_tutor_test_bot?start=" in text

    await dp.feed_update(bot, command_update(ADMIN_TG_ID, "/admin invites"))
    assert bot.sent_texts()[-1].startswith(texts_ru.ADMIN_INVITES_HEADER)

    async with sessionmaker() as s:
        invites = await invite_service.list_invites(s)
        assert len(invites) == 1 and invites[0].max_uses == 3


async def test_admin_command_denied_to_learner_and_stranger(
    dp: Dispatcher, bot: MockedBot, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await dp.feed_update(bot, command_update(fresh_tg_id(), "/admin invite"))
    assert bot.sent_texts()[-1] == texts_ru.NOT_REGISTERED

    code = await _make_invite(sessionmaker)
    tg_id = fresh_tg_id()
    await dp.feed_update(bot, command_update(tg_id, f"/start {code}"))
    await dp.feed_update(bot, command_update(tg_id, "/admin invite"))
    assert bot.sent_texts()[-1] == texts_ru.ADMIN_ONLY


async def test_help_is_available_to_anyone(dp: Dispatcher, bot: MockedBot) -> None:
    await dp.feed_update(bot, command_update(fresh_tg_id(), "/help"))
    assert bot.sent_texts() == [texts_ru.HELP]


async def test_learning_commands_require_registration(dp: Dispatcher, bot: MockedBot) -> None:
    """The Phase 0 placeholder is gone: /today now runs the real handler, which asks a stranger to
    come in through an invite rather than pretending the feature does not exist yet."""
    for command in ("/today", "/stats", "/settings"):
        await dp.feed_update(bot, command_update(fresh_tg_id(), command))
        assert bot.sent_texts()[-1] == texts_ru.NOT_REGISTERED


async def test_choosing_a_reminder_time_makes_the_cron_able_to_select_the_learner(
    dp: Dispatcher, bot: MockedBot, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The gap this closes: reminder_time was NULL for everyone, so no reminder could ever fire."""
    from app.bot.callbacks import SetReminder
    from app.services import reminder_service
    from tests.helpers.updates import callback_update

    code = await _make_invite(sessionmaker)
    tg_id = fresh_tg_id()
    await dp.feed_update(bot, command_update(tg_id, f"/start {code}"))

    async with sessionmaker() as s:
        user = await user_service.get_by_tg_id(s, tg_id)
        assert user is not None and user.reminder_time is None
        assert await reminder_service.reminder_candidates(s) == []

    await dp.feed_update(bot, callback_update(tg_id, SetReminder(hour=20).pack()))

    async with sessionmaker() as s:
        user = await user_service.get_by_tg_id(s, tg_id)
        assert user is not None
        assert user.reminder_time is not None and user.reminder_time.hour == 20
        assert [u.id for u in await reminder_service.reminder_candidates(s)] == [user.id]


async def test_a_learner_can_decline_reminders(
    dp: Dispatcher, bot: MockedBot, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    from app.bot.callbacks import SetReminder
    from tests.helpers.updates import callback_update

    code = await _make_invite(sessionmaker)
    tg_id = fresh_tg_id()
    await dp.feed_update(bot, command_update(tg_id, f"/start {code}"))
    await dp.feed_update(bot, callback_update(tg_id, SetReminder(hour=20).pack()))
    await dp.feed_update(bot, callback_update(tg_id, SetReminder(hour=-1).pack()))

    async with sessionmaker() as s:
        user = await user_service.get_by_tg_id(s, tg_id)
        assert user is not None and user.reminder_time is None
