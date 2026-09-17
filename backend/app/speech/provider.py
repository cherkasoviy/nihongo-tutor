"""The synthesis provider behind a Protocol, with a fake for tests.

Every provider in this project sits behind a Protocol with a fake implementation, so the suite
never needs a real key and CI never makes a paid call. The fake is not a stub: it produces
deterministic bytes derived from the text, so a test can assert that two different texts really do
produce different audio and that the same text does not.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from app.speech.keys import Encoding, Ssml


@dataclass(frozen=True, slots=True)
class Clip:
    """One synthesis result: the same utterance in both encodings.

    ``billed_chars`` is what the provider actually sent, not what the caller asked for. Only the
    provider knows: Google counts every character of the request including SSML tags, and bills each
    encoding as its own request. Reporting it from here is what keeps the ledger honest.
    """

    mp3: bytes
    ogg: bytes
    billed_chars: int
    duration_ms: int | None = None
    timepoints: dict[str, Any] | None = None

    def data(self, encoding: Encoding) -> bytes:
        return self.mp3 if encoding is Encoding.mp3 else self.ogg


class SpeechProvider(Protocol):
    """What the rest of the app needs from a text-to-speech engine."""

    @property
    def name(self) -> str:
        """Goes into the cache key, so changing engine cannot serve the old engine's audio."""
        ...

    async def synthesize(self, *, text: str, voice: str, rate: float, ssml: Ssml) -> Clip: ...


class FakeTTS:
    """Deterministic bytes, no network, no key.

    Counts calls so a test can prove the cache is actually preventing synthesis rather than merely
    returning the right answer twice.
    """

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float, Ssml]] = []

    async def synthesize(self, *, text: str, voice: str, rate: float, ssml: Ssml) -> Clip:
        self.calls.append((text, voice, rate, ssml))
        seed = f"{self.name}|{voice}|{rate:.2f}|{ssml.value}|{text}".encode()
        digest = hashlib.sha256(seed).digest()
        return Clip(
            mp3=b"FAKEMP3" + digest,
            ogg=b"FAKEOGG" + digest,
            billed_chars=2 * len(text),  # one request per encoding, as the real provider does
            duration_ms=100 * max(1, len(text)),
            timepoints=None,
        )
