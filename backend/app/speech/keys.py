"""The cache key, and where a clip lives on disk.

``audio_assets.hash`` is the content address. Everything that changes the bytes is in it, so the
cache invalidates itself: change a voice, a rate, the SSML template or a single character and the
key changes, which makes stale audio impossible by construction rather than by discipline.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Final


class Encoding(StrEnum):
    """Both are generated for every clip, from the same text.

    MP3 for the Mini App: iOS Safari and the WebView Telegram uses do not reliably play Ogg/Opus,
    and audio that works on Android while failing silently on an iPhone is a bug you will not
    reproduce. OGG_OPUS for Telegram voice messages, which is what the Bot API wants natively.
    """

    mp3 = "mp3"
    ogg = "ogg"


class Ssml(StrEnum):
    """Which SSML template wrapped the text.

    Part of the key because the slow variant is the same text at a different ``prosody rate``: with
    the template out of the key, normal and slow would be one row and whichever was synthesised
    first would be served for both.
    """

    plain = "plain/v1"
    slow = "slow/v1"


SLOW_RATE: Final = 0.7


def clip_hash(*, provider: str, voice: str, rate: float, ssml: Ssml, text: str) -> str:
    """``sha256(provider|voice|rate|ssml_version|text)``, as specified in docs/PLAN.md.

    ``rate`` is formatted to two decimals rather than repr'd: ``0.7`` and ``0.70`` must not be two
    different cache entries for the same audio.
    """
    material = f"{provider}|{voice}|{rate:.2f}|{ssml.value}|{text}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def clip_path(audio_dir: str | Path, digest: str, encoding: Encoding) -> Path:
    return Path(audio_dir) / f"{digest}.{encoding.value}"
