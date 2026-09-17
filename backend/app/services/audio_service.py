"""Lazy synthesis, content-addressed on disk, metered against a monthly ceiling.

A caller asks for the audio of some text; this returns a clip, synthesising only if neither the
row nor the file is already there. The cache is the ``audio_assets`` row plus the file it points
at, and both have to exist — a row whose file was lost to a restore is a cache miss, not a 404.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.ai import AiUsageLedger
from app.db.models.audio import AudioAsset
from app.speech.keys import Encoding, Ssml, clip_hash, clip_path
from app.speech.provider import Clip, SpeechProvider

log = structlog.get_logger(__name__)

TASK: Final = "tts"
PROVIDER_LEDGER_NAME: Final = "google"
# What a <speak><prosody …> wrapper adds, used only for the pre-flight estimate. Google bills SSML
# tags as characters, so the ceiling check has to allow for them even though the plain path sends
# none — under-estimating here would wave through the one request that crosses the line.
SSML_OVERHEAD_CHARS: Final = 45


class QuotaExceeded(RuntimeError):
    """The monthly character ceiling was hit.

    Full coverage of every seed is roughly 4,200 characters against a 1,000,000-character monthly
    tier, so the only way to reach this is a bug — a loop synthesising the same text with a
    changing key, most likely. Failing loudly is the point: the alternative is a quiet bill.
    """


@dataclass(frozen=True, slots=True)
class StoredClip:
    hash: str
    mp3_path: str
    ogg_path: str
    synthesized: bool

    def path_for(self, encoding: Encoding) -> str:
        return self.mp3_path if encoding is Encoding.mp3 else self.ogg_path


async def chars_this_month(session: AsyncSession, *, now: dt.datetime) -> int:
    """Characters sent to the TTS API since the first of the month, UTC.

    The provider's free tier resets monthly, so the window matches it rather than a rolling 30 days.
    """
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = await session.scalar(
        select(func.coalesce(func.sum(AiUsageLedger.chars), 0)).where(
            AiUsageLedger.task == TASK, AiUsageLedger.created_at >= start
        )
    )
    return int(total or 0)


async def get_or_create(
    session: AsyncSession,
    *,
    provider: SpeechProvider,
    text: str,
    voice: str,
    rate: float,
    ssml: Ssml = Ssml.plain,
    audio_dir: str,
    monthly_char_ceiling: int,
    now: dt.datetime,
) -> StoredClip:
    """The clip for this text, synthesising it only if it is not already on disk."""
    digest = clip_hash(provider=provider.name, voice=voice, rate=rate, ssml=ssml, text=text)
    mp3 = clip_path(audio_dir, digest, Encoding.mp3)
    ogg = clip_path(audio_dir, digest, Encoding.ogg)

    row = await session.scalar(select(AudioAsset).where(AudioAsset.hash == digest))
    if row is not None and mp3.exists() and ogg.exists():
        return StoredClip(hash=digest, mp3_path=str(mp3), ogg_path=str(ogg), synthesized=False)

    spent = await chars_this_month(session, now=now)
    # Deliberately check-then-act, with no lock. Two concurrent misses on *different* texts could
    # both pass this and both synthesise, overshooting the ceiling by one request. That is fine and
    # is meant to stay fine: the ceiling exists to stop a runaway loop, not to enforce a budget to
    # the character, and full seed coverage is ~4,200 characters against a 200,000 ceiling. Anyone
    # reaching for a lock here should first have a reason the overshoot actually costs something.
    #
    # Checked before the call against a conservative estimate, then recorded from what the provider
    # says it actually sent. Estimating low here would let a single oversized request through; the
    # ledger is what has to be exact, and only the provider knows the real figure.
    estimate = 2 * (len(text) + SSML_OVERHEAD_CHARS)
    if spent + estimate > monthly_char_ceiling:
        raise QuotaExceeded(
            f"{spent} characters already synthesised this month; about {estimate} more would pass "
            f"the {monthly_char_ceiling} ceiling"
        )

    clip: Clip = await provider.synthesize(text=text, voice=voice, rate=rate, ssml=ssml)
    mp3.parent.mkdir(parents=True, exist_ok=True)
    mp3.write_bytes(clip.mp3)
    ogg.write_bytes(clip.ogg)

    if row is None:
        row = AudioAsset(hash=digest, provider=provider.name, voice=voice, rate=rate, text=text)
        session.add(row)
    row.mp3_path = str(mp3)
    row.ogg_path = str(ogg)
    row.duration_ms = clip.duration_ms
    row.timepoints = clip.timepoints
    session.add(
        AiUsageLedger(
            user_id=None,
            task=TASK,
            provider=PROVIDER_LEDGER_NAME,
            model=voice,
            chars=clip.billed_chars,
            cost_usd=0,
            # Stamped with the caller's clock, not the database's. ``chars_this_month`` computes its
            # window from the same ``now``, and a ledger whose rows are timed by a different clock
            # than the window that reads them is an accounting error waiting for the two to drift.
            created_at=now,
        )
    )
    await session.flush()
    log.info("clip synthesised", hash=digest, chars=clip.billed_chars, voice=voice, ssml=ssml.value)
    return StoredClip(hash=digest, mp3_path=str(mp3), ogg_path=str(ogg), synthesized=True)
