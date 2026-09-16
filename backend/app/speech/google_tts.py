"""Google Cloud Text-to-Speech.

Authenticated by the service-account JSON the deployment already needs for Vertex and STT —
``GOOGLE_APPLICATION_CREDENTIALS`` — so no separate key exists for audio.

Two calls per clip, one per encoding, because the API returns one container per request. That is
the price of shipping MP3 and OGG_OPUS from the same text, and at roughly 4.2k characters for full
coverage it does not come close to mattering.
"""

from __future__ import annotations

import asyncio
from typing import Final

from google.cloud import texttospeech as tts

from app.speech.keys import SLOW_RATE, Encoding, Ssml
from app.speech.provider import Clip

LANGUAGE_CODE: Final = "ja-JP"

_ENCODINGS: Final = {
    Encoding.mp3: tts.AudioEncoding.MP3,
    Encoding.ogg: tts.AudioEncoding.OGG_OPUS,
}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wrap_ssml(text: str, ssml: Ssml) -> str:
    """The document to send. SSML **only** for the slow variant.

    Google bills every character of the request, and its pricing page is explicit that SSML tags
    count too (all but ``<mark>``). A bare ``<speak></speak>`` is 15 characters, and with one
    request per encoding that is 18,060 billed characters across full seed coverage against 4,244
    of actual Japanese — the markup would cost 4.3x the content it wraps.

    So the normal case is sent as plain text, which needs no wrapper at all. Only the slow variant
    pays for markup, because a ``prosody`` rate is the thing that makes it slow.
    """
    if ssml is Ssml.plain:
        return text
    return f'<speak><prosody rate="{SLOW_RATE:.0%}">{_escape(text)}</prosody></speak>'


class GoogleTTS:
    """Synchronous client driven from a thread, which is what the library supports."""

    name = "google"

    def __init__(self, client: tts.TextToSpeechClient | None = None) -> None:
        self._client = client or tts.TextToSpeechClient()

    async def synthesize(self, *, text: str, voice: str, rate: float, ssml: Ssml) -> Clip:
        document = wrap_ssml(text, ssml)
        mp3, ogg = await asyncio.gather(
            self._one(document, voice, rate, Encoding.mp3, ssml),
            self._one(document, voice, rate, Encoding.ogg, ssml),
        )
        # Two requests, each billed in full. The API returns one container per call, so shipping
        # both MP3 and OGG_OPUS costs twice — which is real and worth the iOS playback it buys.
        return Clip(mp3=mp3, ogg=ogg, billed_chars=2 * len(document))

    async def _one(self, document: str, voice: str, rate: float, encoding: Encoding, ssml: Ssml) -> bytes:
        payload = tts.SynthesisInput(text=document) if ssml is Ssml.plain else tts.SynthesisInput(ssml=document)

        def call() -> bytes:
            response = self._client.synthesize_speech(
                input=payload,
                voice=tts.VoiceSelectionParams(language_code=LANGUAGE_CODE, name=voice),
                audio_config=tts.AudioConfig(audio_encoding=_ENCODINGS[encoding], speaking_rate=rate),
            )
            return bytes(response.audio_content)

        return await asyncio.to_thread(call)


def list_japanese_voices(client: tts.TextToSpeechClient | None = None) -> list[tuple[str, str]]:
    """Every ``ja-JP`` voice the project can actually use, as ``(name, gender)``.

    Exists because ``docs/CONTENT.md`` says to verify the voice id rather than assume one, and
    because this is also the cheapest proof that the service account's credentials and roles work:
    it is a real authenticated call that costs nothing.
    """
    api = client or tts.TextToSpeechClient()
    voices = api.list_voices(language_code=LANGUAGE_CODE).voices
    return sorted((v.name, tts.SsmlVoiceGender(v.ssml_gender).name) for v in voices)
