"""``/start`` and ``/admin`` through the real Dispatcher with a network-less bot."""

import pytest
from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import texts_ru
from app.bot.dispatcher import create_dispatcher
from app.config import get_settings
from app.services import invite_service, user_service
from tests.conftest import ADMIN_TG_ID, fresh_tg_id
from tests.helpers.mocked_bot import MockedBot
from tests.helpers.updates import command_update

pytestmark = pytest.mark.integration


@pytest.fixture
def bot() -> MockedBot:
    return MockedBot()


@pytest.fixture(scope="session")
def _dispatcher(migrated_database_url: str) -> Dispatcher:
    # Routers are module-level singletons and can be attached to one Dispatcher only.
    return create_dispatcher(get_settings(), sessionmaker=None)  # type: ignore[arg-type]


@pytest.fixture
def dp(_dispatcher: Dispatcher, sessionmaker: async_sessionmaker[AsyncSession]) -> Dispatcher:
    _dispatcher["sessionmaker"] = sessionmaker
    return _dispatcher


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
    assert len(texts) == 1 and texts[0].startswith("Привет, Аня!")
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
    assert bot.sent_texts() == [texts_ru.WELCOME_ADMIN.format(name="Игорь")]
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
