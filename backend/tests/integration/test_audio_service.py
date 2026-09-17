"""Lazy synthesis: cache hits must not call the provider, and the ceiling must actually stop it."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.ai import AiUsageLedger
from app.db.models.audio import AudioAsset
from app.services import audio_service
from app.speech.keys import Ssml
from app.speech.provider import FakeTTS

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 4, 10, 9, 0, tzinfo=dt.UTC)
VOICE = "ja-JP-Neural2-B"


async def _get(
    sessionmaker: async_sessionmaker[AsyncSession],
    provider: FakeTTS,
    audio_dir: Path,
    text: str = "みず",
    ssml: Ssml = Ssml.plain,
    ceiling: int = 1000,
    now: dt.datetime = NOW,
) -> audio_service.StoredClip:
    async with sessionmaker() as s:
        clip = await audio_service.get_or_create(
            s,
            provider=provider,
            text=text,
            voice=VOICE,
            rate=1.0,
            ssml=ssml,
            audio_dir=str(audio_dir),
            monthly_char_ceiling=ceiling,
            now=now,
        )
        await s.commit()
        return clip


async def test_a_second_request_is_served_from_disk(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    tts = FakeTTS()
    first = await _get(sessionmaker, tts, tmp_path)
    second = await _get(sessionmaker, tts, tmp_path)

    assert first.hash == second.hash
    assert first.synthesized and not second.synthesized
    assert len(tts.calls) == 1, "the cache did not prevent a second synthesis"
    assert Path(first.mp3_path).exists() and Path(first.ogg_path).exists()


async def test_both_encodings_are_written_from_one_synthesis(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """iOS will not reliably play Ogg in a Telegram WebView, and that failure is invisible from
    an Android phone — so the MP3 has to exist, from the same text, every time."""
    clip = await _get(sessionmaker, FakeTTS(), tmp_path)
    mp3, ogg = Path(clip.mp3_path).read_bytes(), Path(clip.ogg_path).read_bytes()
    assert mp3.startswith(b"FAKEMP3") and ogg.startswith(b"FAKEOGG")
    assert mp3 != ogg

    async with sessionmaker() as s:
        row = await s.scalar(select(AudioAsset).where(AudioAsset.hash == clip.hash))
    assert row is not None and row.mp3_path and row.ogg_path, "one row carries both encodings"


async def test_a_lost_file_is_a_cache_miss_not_a_broken_row(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """backup.sh tarballs the audio volume separately from the database, so the two can come back
    out of step. A row whose file is missing has to re-synthesise, not serve a path to nothing."""
    tts = FakeTTS()
    clip = await _get(sessionmaker, tts, tmp_path)
    Path(clip.ogg_path).unlink()

    again = await _get(sessionmaker, tts, tmp_path)
    assert again.synthesized and len(tts.calls) == 2
    assert Path(again.ogg_path).exists()


async def test_the_slow_variant_is_a_separate_clip(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    tts = FakeTTS()
    normal = await _get(sessionmaker, tts, tmp_path, ssml=Ssml.plain)
    slow = await _get(sessionmaker, tts, tmp_path, ssml=Ssml.slow)
    assert normal.hash != slow.hash
    assert Path(normal.mp3_path).read_bytes() != Path(slow.mp3_path).read_bytes()


async def test_every_synthesis_is_metered(sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path) -> None:
    tts = FakeTTS()
    await _get(sessionmaker, tts, tmp_path, text="みず")
    await _get(sessionmaker, tts, tmp_path, text="みず")  # cached, must not be counted twice
    await _get(sessionmaker, tts, tmp_path, text="これは何ですか")

    async with sessionmaker() as s:
        rows = list(await s.scalars(select(AiUsageLedger).where(AiUsageLedger.task == "tts")))
        spent = await audio_service.chars_this_month(s, now=NOW)
    assert len(rows) == 2, "a cache hit must not appear in the ledger"
    # Twice the text, because each encoding is its own billed request. The ledger records what the
    # provider says it sent, not what the caller asked for.
    assert spent == 2 * (len("みず") + len("これは何ですか"))


async def test_the_ceiling_stops_synthesis_rather_than_billing_for_it(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """Full coverage is ~4,200 characters against a 1M tier, so hitting this means a bug — and a
    bug that loops must fail loudly rather than run up a quiet bill."""
    tts = FakeTTS()
    await _get(sessionmaker, tts, tmp_path, text="あいうえお", ceiling=105)
    with pytest.raises(audio_service.QuotaExceeded):
        await _get(sessionmaker, tts, tmp_path, text="かきくけこ", ceiling=105)
    assert len(tts.calls) == 1, "the provider was called despite the ceiling"


async def test_the_ceiling_counts_this_month_only(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """The provider's tier resets monthly, so the window has to match it."""
    tts = FakeTTS()
    await _get(sessionmaker, tts, tmp_path, text="あいうえお", ceiling=105)
    next_month = NOW.replace(month=5)
    clip = await _get(sessionmaker, tts, tmp_path, text="かきくけこ", ceiling=105, now=next_month)
    assert clip.synthesized, "last month's characters must not block this month"
