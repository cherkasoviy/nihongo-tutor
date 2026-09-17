"""Voice in Telegram: reuse the file id, and never let audio take a lesson down."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import voice
from app.config import Settings, get_settings
from app.content_pipeline.import_kana import import_kana
from app.db.models.audio import AudioAsset
from app.services import audio_service
from app.speech.keys import Ssml, clip_hash
from app.speech.provider import FakeTTS

pytestmark = pytest.mark.integration


class FakeVoice:
    def __init__(self, file_id: str) -> None:
        self.file_id = file_id


class FakeSent:
    def __init__(self, file_id: str) -> None:
        self.voice = FakeVoice(file_id)


class FakeMessage:
    """Just enough Message to record what the bot tried to send.

    ``reject_ids`` models the thing that actually happens: Telegram refuses a file id minted by a
    different bot, but accepts the bytes.
    """

    def __init__(self, *, fail: bool = False, reject_ids: bool = False, mint: str = "tg-file-id-1") -> None:
        self.bot = object()
        self.sent: list[Any] = []
        self.fail = fail
        self.reject_ids = reject_ids
        self.mint = mint

    async def answer_voice(self, *, voice: Any, caption: str | None = None, reply_markup: Any = None) -> FakeSent:
        from aiogram.exceptions import TelegramAPIError

        if self.fail:
            raise TelegramAPIError(method=None, message="nope")  # type: ignore[arg-type]
        self.sent.append(voice)
        if self.reject_ids and isinstance(voice, str):
            raise TelegramAPIError(method=None, message="wrong file identifier")  # type: ignore[arg-type]
        return FakeSent(self.mint)


def _settings(tmp_path: Path) -> Settings:
    s = get_settings()
    s.audio_dir = str(tmp_path)
    return s


async def test_the_second_send_reuses_the_telegram_file_id(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without this every syllable is re-uploaded on every send — the same 5 KB, hundreds of times
    a week. This is the column audio_assets.tg_file_id exists for."""
    async with sessionmaker() as s:
        await import_kana(s)
        await s.commit()

    tts = FakeTTS()
    monkeypatch.setattr("app.bot.voice._provider", lambda: tts)
    settings = _settings(tmp_path)

    first = FakeMessage()
    async with sessionmaker() as s:
        await voice.send_voice(first, s, settings=settings, text="あ", caption="card")  # type: ignore[arg-type]
        await s.commit()

    second = FakeMessage()
    async with sessionmaker() as s:
        await voice.send_voice(second, s, settings=settings, text="あ", caption="card")  # type: ignore[arg-type]
        await s.commit()

    assert not isinstance(first.sent[0], str), "the first send must upload the bytes"
    assert second.sent[0] == "tg-file-id-1", "the second send must reuse the id Telegram gave back"
    assert len(tts.calls) == 1, "and neither send may re-synthesise"

    async with sessionmaker() as s:
        rows = list(await s.scalars(select(AudioAsset.tg_file_id)))
    assert "tg-file-id-1" in rows


async def test_a_telegram_failure_does_not_take_the_lesson_down(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audio is an addition to the card, never a precondition for it."""
    monkeypatch.setattr("app.bot.voice._provider", FakeTTS)
    async with sessionmaker() as s:
        await import_kana(s)
        await s.commit()

    async with sessionmaker() as s:
        sent = await voice.send_voice(
            FakeMessage(fail=True),  # type: ignore[arg-type]
            s,
            settings=_settings(tmp_path),
            text="あ",
        )
    assert sent is None, "a refusal must be reported as 'no voice', not raised"


async def test_the_quota_ceiling_silences_rather_than_raises(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(*_a: object, **_k: object) -> None:
        raise audio_service.QuotaExceeded("ceiling")

    monkeypatch.setattr("app.bot.voice.audio_service.get_or_create", refuse)
    async with sessionmaker() as s:
        sent = await voice.send_voice(
            FakeMessage(), s, settings=_settings(tmp_path), text="あ"  # type: ignore[arg-type]
        )
    assert sent is None


async def test_no_credentials_at_all_still_teaches(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Building the TTS client is itself a thing that fails — on a machine with no credentials it
    raises DefaultCredentialsError. That must be a silent lesson, not a dead one, which means the
    construction has to happen inside the guard rather than at the call site."""

    def no_credentials() -> FakeTTS:
        raise RuntimeError("Your default credentials were not found")

    monkeypatch.setattr("app.bot.voice._provider", no_credentials)
    async with sessionmaker() as s:
        await import_kana(s)
        await s.commit()
    async with sessionmaker() as s:
        sent = await voice.send_voice(
            FakeMessage(), s, settings=_settings(tmp_path), text="あ"  # type: ignore[arg-type]
        )
    assert sent is None


async def test_a_rejected_file_id_re_uploads_and_heals_the_row(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File ids are per-bot, and the documented cutover restores a pg_dump into a *different* bot —
    so every id the dev bot minted arrives at the prod bot dead. Without a retry each of those clips
    falls back to text on every send, forever, and looks like a one-off Telegram hiccup in the logs.
    """
    tts = FakeTTS()
    monkeypatch.setattr("app.bot.voice._provider", lambda: tts)
    settings = _settings(tmp_path)
    async with sessionmaker() as s:
        await import_kana(s)
        await s.commit()

    # One good send, then poison the stored id the way a restore from another bot would.
    async with sessionmaker() as s:
        await voice.send_voice(FakeMessage(), s, settings=settings, text="あ")  # type: ignore[arg-type]
        await s.commit()
    # Addressed by digest, not "the only row": the suite shares one database and other tests
    # synthesise too.
    digest = clip_hash(provider=tts.name, voice=settings.tts_voice, rate=settings.tts_rate, ssml=Ssml.plain, text="あ")
    async with sessionmaker() as s:
        row = await s.scalar(select(AudioAsset).where(AudioAsset.hash == digest))
        assert row is not None
        row.tg_file_id = "dead-id-from-the-dev-bot"
        await s.commit()

    message = FakeMessage(reject_ids=True, mint="tg-file-id-fresh")
    async with sessionmaker() as s:
        sent = await voice.send_voice(message, s, settings=settings, text="あ")  # type: ignore[arg-type]
        await s.commit()

    assert sent is not None, "the voice must still arrive"
    assert message.sent[0] == "dead-id-from-the-dev-bot", "it should try the cached id first"
    assert not isinstance(message.sent[1], str), "then fall back to the bytes"
    assert len(tts.calls) == 1, "and re-uploading must not re-synthesise"

    async with sessionmaker() as s:
        healed = await s.scalar(select(AudioAsset.tg_file_id).where(AudioAsset.hash == digest))
    assert healed == "tg-file-id-fresh", "the dead id must be overwritten, not merely supplemented"


async def test_a_clip_that_fails_both_ways_falls_back_to_text(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retry must not turn a genuine outage into an exception."""
    monkeypatch.setattr("app.bot.voice._provider", FakeTTS)
    async with sessionmaker() as s:
        await import_kana(s)
        await s.commit()
    async with sessionmaker() as s:
        sent = await voice.send_voice(
            FakeMessage(fail=True), s, settings=_settings(tmp_path), text="あ"  # type: ignore[arg-type]
        )
    assert sent is None
