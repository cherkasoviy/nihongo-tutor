"""The webhook endpoint must reject requests without Telegram's secret header before touching the bot."""

import httpx
import pytest

from app.config import Settings
from app.main import create_app

UPDATE = {"update_id": 1, "message": {"message_id": 1, "date": 0, "chat": {"id": 1, "type": "private"}, "text": "hi"}}

# The secret the app actually resolved: the environment (CI sets its own WEBHOOK_SECRET) wins over
# both conftest's default and a developer's local .env, so read it back instead of assuming.
CONFIGURED_SECRET = Settings().webhook_secret.get_secret_value()


@pytest.fixture
def client() -> httpx.AsyncClient:
    app = create_app(Settings())  # lifespan is not run: no bot, no DB needed
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_missing_secret_is_forbidden(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.post("/tg/webhook", json=UPDATE)
    assert resp.status_code == 403


async def test_wrong_secret_is_forbidden(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.post("/tg/webhook", json=UPDATE, headers={"X-Telegram-Bot-Api-Secret-Token": "nope"})
    assert resp.status_code == 403


async def test_right_secret_without_bot_is_unavailable(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.post(
            "/tg/webhook", json=UPDATE, headers={"X-Telegram-Bot-Api-Secret-Token": CONFIGURED_SECRET}
        )
    assert resp.status_code == 503


async def test_api_requires_bearer(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
