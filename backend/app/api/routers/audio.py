"""Audio for the things the learner is looking at.

Two URLs, deliberately. The **item** URL says which syllable; the **clip** URL says which bytes.
Only the second is content-addressed, and only the second may be cached hard.

That split is not decoration. The digest covers the voice, the rate and the SSML template, so the
server's cache invalidates itself the moment any of those change. An ``immutable`` header on an
item URL would undo exactly that one hop later: change ``TTS_VOICE`` and every device that had ever
tapped a syllable would keep playing the old voice for a year, with no way to reach it.

Synthesis is lazy — a clip missing from disk is made on request and cached — so a deploy that has
not run ``warm-audio`` still works, just slower on the first tap.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from functools import lru_cache
from typing import Annotated, Final

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select

from app.api.deps import CurrentUser
from app.config import get_settings
from app.db.base import SessionDep
from app.db.models.content import Item, ItemType, Kana
from app.logging import get_logger
from app.services import audio_service
from app.speech.keys import Encoding, Ssml, clip_path
from app.speech.provider import SpeechProvider

log = get_logger(__name__)

router = APIRouter(prefix="/audio", tags=["audio"])

MEDIA_TYPES: Final = {Encoding.mp3: "audio/mpeg", Encoding.ogg: "audio/ogg"}
DIGEST: Final = re.compile(r"^[0-9a-f]{64}$")

# The clip URL names its own bytes, so they can never become stale: anything that would change them
# changes the digest and therefore the URL. ``private`` rather than ``public`` because the route is
# authenticated — a syllable is not a secret, but a shared cache has no business holding a response
# to a request that carried an Authorization header.
IMMUTABLE: Final[dict[str, str]] = {"Cache-Control": "private, max-age=31536000, immutable"}
# The item URL resolves to whichever clip is current, which is precisely what a voice change moves.
RESOLVE_ONLY: Final[dict[str, str]] = {"Cache-Control": "private, no-cache"}


@lru_cache(maxsize=1)
def _provider() -> SpeechProvider:
    """Built once, on first use.

    Not at import, so a deployment without credentials fails on the first audio tap rather than
    refusing to boot. Not per request either: constructing a ``TextToSpeechClient`` loads
    credentials and sets up a gRPC channel, and almost every request is a cache hit that needs
    nothing from the provider but its name. ``lru_cache`` does not memoise exceptions, so a
    genuinely broken credential keeps failing rather than caching its own failure.
    """
    from app.speech.google_tts import GoogleTTS

    return GoogleTTS()


def _encoding(ext: str) -> Encoding:
    try:
        return Encoding(ext)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown audio format") from None


@router.get("/kana/{item_id}.{ext}")
async def kana_audio(
    item_id: uuid.UUID,
    ext: str,
    user: CurrentUser,
    session: SessionDep,
    slow: Annotated[bool, Query(description="The SSML prosody-rate variant")] = False,
) -> RedirectResponse:
    """Resolve a syllable to its current clip, synthesising it if this is the first ask.

    Sends the ``reading``, never the written form — the rule the whole content design rests on. For
    kana the two coincide; routing it through the same field as everything else is what stops the
    first vocabulary item being the place someone sends 日本 and gets にっぽん.
    """
    encoding = _encoding(ext)
    row = (
        await session.execute(
            select(Kana).join(Item, (Item.ref_id == Kana.id) & (Item.type == ItemType.kana)).where(Item.id == item_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such syllable")

    settings = get_settings()
    try:
        clip = await audio_service.get_or_create(
            session,
            provider=_provider(),
            text=row.char,
            voice=settings.tts_voice,
            rate=settings.tts_rate,
            ssml=Ssml.slow if slow else Ssml.plain,
            audio_dir=settings.audio_dir,
            monthly_char_ceiling=settings.tts_monthly_char_ceiling,
            now=dt.datetime.now(dt.UTC),
        )
        await session.commit()
    except audio_service.QuotaExceeded as err:
        log.warning("audio refused by the monthly ceiling", item_id=str(item_id), error=str(err))
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "audio temporarily unavailable") from None

    # 307 rather than 301/302: the method must survive, and nothing about this mapping is permanent
    # — that is the whole point of not marking it immutable.
    return RedirectResponse(
        url=f"/api/audio/clips/{clip.hash}.{encoding.value}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers=RESOLVE_ONLY,
    )


@router.get("/clips/{digest}.{ext}", response_class=FileResponse)
async def clip_bytes(digest: str, ext: str, user: CurrentUser) -> FileResponse:
    """The bytes themselves, addressed by what they are. Never synthesises."""
    encoding = _encoding(ext)
    if not DIGEST.match(digest):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not a clip address")

    path = clip_path(get_settings().audio_dir, digest, encoding)
    if not path.exists():
        # Reachable if the audio volume is restored behind the database. The item URL will
        # re-synthesise on the next tap; this one deliberately will not.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "clip not stored")
    return FileResponse(path, media_type=MEDIA_TYPES[encoding], headers=IMMUTABLE)
