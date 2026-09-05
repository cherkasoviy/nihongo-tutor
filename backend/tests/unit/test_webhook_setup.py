"""Lifespan registers the webhook only when Telegram reports a different URL."""

from aiogram.types import WebhookInfo

from app.config import Settings
from app.main import _setup_webhook
from tests.helpers.mocked_bot import MockedBot


def _settings() -> Settings:
    return Settings(public_url="https://tutor.example.org", webhook_secret="s3cret-s3cret-s3cret")  # type: ignore[arg-type]


async def test_sets_webhook_with_secret_when_missing() -> None:
    bot = MockedBot()
    bot.mocked.add_result(WebhookInfo(url="", has_custom_certificate=False, pending_update_count=0))
    await _setup_webhook(bot, _settings())

    methods = [m.__api_method__ for m in bot.mocked.requests]
    assert methods == ["getWebhookInfo", "setWebhook"]
    set_call = bot.mocked.requests[-1]
    assert set_call.url == "https://tutor.example.org/tg/webhook"  # type: ignore[attr-defined]
    assert set_call.secret_token == "s3cret-s3cret-s3cret"  # type: ignore[attr-defined]
    assert set_call.allowed_updates == ["message", "callback_query"]  # type: ignore[attr-defined]


async def test_keeps_webhook_when_already_registered() -> None:
    bot = MockedBot()
    bot.mocked.add_result(
        WebhookInfo(url="https://tutor.example.org/tg/webhook", has_custom_certificate=False, pending_update_count=3)
    )
    await _setup_webhook(bot, _settings())
    assert [m.__api_method__ for m in bot.mocked.requests] == ["getWebhookInfo"]
