"""The audio endpoint: synthesise on demand, serve bytes, and never show the ceiling as a 500."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.content_pipeline.import_kana import import_kana
from app.db.models import UserRole
from app.db.models.content import Item, ItemType
from app.main import create_app
from app.services import audio_service, user_service
from app.services.user_service import TelegramIdentity
from app.speech.provider import FakeTTS
from tests.conftest import TEST_BOT_TOKEN, fresh_tg_id
from tests.helpers.initdata import make_init_data

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(migrated_database_url: str) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(get_settings())
    app.state.bot_username = "nihongo_tutor_test_bot"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _setup(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> tuple[dict[str, str], str]:
    tg_id = fresh_tg_id()
    async with sessionmaker() as s:
        await import_kana(s)
        await user_service.create_user(
            s,
            TelegramIdentity(tg_user_id=tg_id, first_name="Аня"),
            role=UserRole.learner,
            invited_by=None,
            daily_budget_usd=0.35,
            now=dt.datetime.now(dt.UTC),
        )
        await s.commit()
        item_id = await s.scalar(
            select(Item.id).where(Item.type == ItemType.kana).order_by(Item.curriculum_order).limit(1)
        )
    init_data = make_init_data(
        TEST_BOT_TOKEN, tg_user_id=tg_id, auth_date=int(dt.datetime.now(dt.UTC).timestamp()) - 30
    )
    resp = await client.post("/api/auth/telegram", json={"init_data": init_data})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}, str(item_id)


async def test_a_syllable_can_be_heard(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    fake = FakeTTS()
    monkeypatch.setattr("app.api.routers.audio._provider", lambda: fake)
    monkeypatch.setattr(get_settings(), "audio_dir", str(tmp_path))
    headers, item_id = await _setup(client, sessionmaker)

    res = await client.get(f"/api/audio/kana/{item_id}.mp3", headers=headers)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "audio/mpeg"
    assert res.content.startswith(b"FAKEMP3")
    assert "immutable" in res.headers.get("cache-control", "")


async def test_a_second_tap_does_not_synthesise_again(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    fake = FakeTTS()
    monkeypatch.setattr("app.api.routers.audio._provider", lambda: fake)
    monkeypatch.setattr(get_settings(), "audio_dir", str(tmp_path))
    headers, item_id = await _setup(client, sessionmaker)

    assert (await client.get(f"/api/audio/kana/{item_id}.mp3", headers=headers)).status_code == 200
    assert (await client.get(f"/api/audio/kana/{item_id}.mp3", headers=headers)).status_code == 200
    assert len(fake.calls) == 1, "tapping twice must not cost two syntheses"


async def test_audio_requires_a_learner(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Synthesis costs money. An unauthenticated caller must not be able to spend it."""
    _headers, item_id = await _setup(client, sessionmaker)
    assert (await client.get(f"/api/audio/kana/{item_id}.mp3")).status_code in (401, 403)


async def test_an_unknown_format_is_a_404_not_a_crash(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    headers, item_id = await _setup(client, sessionmaker)
    assert (await client.get(f"/api/audio/kana/{item_id}.wav", headers=headers)).status_code == 404


async def test_the_quota_ceiling_degrades_rather_than_erroring(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A learner who taps a syllable should be told audio is unavailable, not shown a 500."""

    async def refuse(*_a: object, **_k: object) -> None:
        raise audio_service.QuotaExceeded("ceiling")

    monkeypatch.setattr("app.api.routers.audio._provider", FakeTTS)
    monkeypatch.setattr("app.api.routers.audio.audio_service.get_or_create", refuse)
    headers, item_id = await _setup(client, sessionmaker)

    res = await client.get(f"/api/audio/kana/{item_id}.mp3", headers=headers)
    assert res.status_code == 503
