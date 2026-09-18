"""Pre-generate every clip the seeds imply, so no learner is the one who waits.

Synthesis is lazy by default, which is the right behaviour for anything the seeds do not cover.
But the first tap on a cold cache costs a network round trip to Google, and the learner who pays it
is whoever opens the app first after a deploy. Warming is cheap — full coverage is a few thousand
characters — so there is no reason for that to be anyone.

What gets synthesised follows ``docs/CONTENT.md`` exactly: the *reading* for anything isolated, a
sentence as written. For kana those coincide. For vocabulary they do not, and the asymmetry is the
whole point — 日本 is にほん or にっぽん depending on context, so the headword is sent as にほん,
while 日本語を勉強します。 goes as written because the engine's sentence analysis is what makes は
read as *wa* rather than *ha*.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.content_pipeline.kana_seed import load_all
from app.content_pipeline.vocab_seed import load_vocab
from app.logging import get_logger
from app.services import audio_service
from app.speech.keys import Ssml
from app.speech.provider import SpeechProvider

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WarmReport:
    synthesized: int = 0
    already_cached: int = 0
    chars: int = 0

    def __add__(self, other: WarmReport) -> WarmReport:
        return WarmReport(
            synthesized=self.synthesized + other.synthesized,
            already_cached=self.already_cached + other.already_cached,
            chars=self.chars + other.chars,
        )


def kana_texts() -> list[str]:
    """Every string the kana cards can speak: each syllable, and each example word."""
    seen: dict[str, None] = {}
    for seed in load_all():
        for entry in seed.kana:
            seen.setdefault(entry.char, None)
            # The example's *reading*, not its written form — the same rule that keeps 日本 from
            # being synthesised as にっぽん. For kana the two coincide.
            if entry.example_reading:
                seen.setdefault(entry.example_reading, None)
    return list(seen)


def vocab_texts() -> list[str]:
    """Every string the vocabulary cards can speak: each headword's reading, each sentence as written.

    Never ``word``. A headword is isolated, so the synthesiser has no context to disambiguate its
    reading from, and a confidently wrong 日本 is the exact failure a beginner cannot catch.
    """
    seen: dict[str, None] = {}
    for entry in load_vocab().vocab:
        seen.setdefault(entry.reading, None)
        seen.setdefault(entry.example.ja, None)
    return list(seen)


def seed_texts() -> list[str]:
    """Everything both seeds imply, deduplicated across them.

    Deduplication is not a micro-optimisation: several kana share an example word, three sentences
    repeat, and a vocabulary reading can equal a kana example. Paying twice for identical audio is
    the exact waste the content-addressed cache exists to prevent, and the cache would collapse
    them anyway — this just avoids the round trips that discover that.
    """
    seen: dict[str, None] = {}
    for text in (*kana_texts(), *vocab_texts()):
        seen.setdefault(text, None)
    return list(seen)


async def warm(
    session: AsyncSession,
    *,
    provider: SpeechProvider,
    voice: str,
    rate: float,
    audio_dir: str,
    monthly_char_ceiling: int,
    now: dt.datetime,
    include_slow: bool = False,
) -> WarmReport:
    """Synthesise everything the seeds imply. Safe to re-run: cached clips are not re-made."""
    variants = [Ssml.plain, Ssml.slow] if include_slow else [Ssml.plain]
    report = WarmReport()
    for text in seed_texts():
        for ssml in variants:
            clip = await audio_service.get_or_create(
                session,
                provider=provider,
                text=text,
                voice=voice,
                rate=rate,
                ssml=ssml,
                audio_dir=audio_dir,
                monthly_char_ceiling=monthly_char_ceiling,
                now=now,
            )
            report += WarmReport(synthesized=1, chars=len(text)) if clip.synthesized else WarmReport(already_cached=1)
    log.info(
        "audio warmed",
        synthesized=report.synthesized,
        cached=report.already_cached,
        chars=report.chars,
    )
    return report
