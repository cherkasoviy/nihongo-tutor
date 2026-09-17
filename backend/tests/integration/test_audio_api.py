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
from app.db.models.content import Item, ItemType, Kana
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

    res = await client.get(f"/api/audio/kana/{item_id}.mp3", headers=headers, follow_redirects=True)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "audio/mpeg"
    assert res.content.startswith(b"FAKEMP3")


async def test_only_the_content_addressed_url_is_cached_forever(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """The digest covers voice, rate and SSML template, so the clip URL can never go stale. The item
    URL is exactly what a voice change has to move, and caching it hard would pin every device that
    ever tapped a syllable to the old voice for a year, unreachably."""
    monkeypatch.setattr("app.api.routers.audio._provider", FakeTTS)
    monkeypatch.setattr(get_settings(), "audio_dir", str(tmp_path))
    headers, item_id = await _setup(client, sessionmaker)

    item = await client.get(f"/api/audio/kana/{item_id}.mp3", headers=headers)
    assert item.status_code == 307
    assert "no-cache" in item.headers["cache-control"]
    assert "immutable" not in item.headers["cache-control"]

    location = item.headers["location"]
    assert location.startswith("/api/audio/clips/")
    clip = await client.get(location, headers=headers)
    assert clip.status_code == 200
    assert "immutable" in clip.headers["cache-control"]
    # Authenticated responses have no business in a shared cache, even for something unsecret.
    assert "private" in clip.headers["cache-control"]
    assert "public" not in clip.headers["cache-control"]


async def test_a_clip_address_that_is_not_a_digest_is_refused(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    headers, _ = await _setup(client, sessionmaker)
    for bad in ("../../etc/passwd", "nothex", "a" * 63):
        res = await client.get(f"/api/audio/clips/{bad}.mp3", headers=headers)
        assert res.status_code == 404, bad


async def test_the_client_is_built_once_not_per_request(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """Almost every request is a cache hit, which needs nothing from the provider but its name.
    Constructing a gRPC client for each of those is pure waste."""
    from app.api.routers import audio as audio_router

    audio_router._provider.cache_clear()
    built = 0

    def factory() -> FakeTTS:
        nonlocal built
        built += 1
        return FakeTTS()

    monkeypatch.setattr("app.speech.google_tts.GoogleTTS", factory)
    monkeypatch.setattr(get_settings(), "audio_dir", str(tmp_path))
    headers, item_id = await _setup(client, sessionmaker)

    for _ in range(3):
        await client.get(f"/api/audio/kana/{item_id}.mp3", headers=headers, follow_redirects=True)
    assert built == 1, f"a client was constructed {built} times for three requests"
    audio_router._provider.cache_clear()


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

    for _ in range(2):
        res = await client.get(f"/api/audio/kana/{item_id}.mp3", headers=headers, follow_redirects=True)
        assert res.status_code == 200
    assert len(fake.calls) == 1, "tapping twice must not cost two syntheses"


async def test_audio_requires_a_learner(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Synthesis costs money. An unauthenticated caller must not be able to spend it."""
    _headers, item_id = await _setup(client, sessionmaker)
    assert (await client.get(f"/api/audio/kana/{item_id}.mp3")).status_code in (401, 403)
    assert (await client.get(f"/api/audio/clips/{'a' * 64}.mp3")).status_code in (401, 403)


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


async def test_the_example_word_can_be_heard_too(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """A syllable in isolation is a shape and a sound; the example is where it becomes a word."""
    fake = FakeTTS()
    monkeypatch.setattr("app.api.routers.audio._provider", lambda: fake)
    monkeypatch.setattr(get_settings(), "audio_dir", str(tmp_path))
    headers, item_id = await _setup(client, sessionmaker)

    char = await client.get(f"/api/audio/kana/{item_id}.mp3", headers=headers, follow_redirects=True)
    example = await client.get(f"/api/audio/kana/{item_id}.mp3?part=example", headers=headers, follow_redirects=True)
    assert char.status_code == 200 and example.status_code == 200
    assert char.content != example.content, "the example must not be the syllable over again"

    spoken = [call[0] for call in fake.calls]
    async with sessionmaker() as s:
        row = await s.scalar(
            select(Kana).join(Item, (Item.ref_id == Kana.id) & (Item.type == ItemType.kana)).where(Item.id == item_id)
        )
    assert row is not None
    # The reading, never the written form. They coincide for kana; the rule is what matters.
    assert row.example_reading in spoken


async def test_a_missing_credential_is_a_503_not_a_500(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Building the client raises DefaultCredentialsError where there is no key. That is a
    deployment fault, not a bad request, and the learner should be told the sound is unavailable."""

    def no_credentials() -> FakeTTS:
        raise RuntimeError("Your default credentials were not found")

    monkeypatch.setattr("app.api.routers.audio._provider", no_credentials)
    headers, item_id = await _setup(client, sessionmaker)

    res = await client.get(f"/api/audio/kana/{item_id}.mp3", headers=headers)
    assert res.status_code == 503
