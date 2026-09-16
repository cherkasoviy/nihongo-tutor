"""Audio for the things the learner is looking at.

Content-addressed on disk, but the client never computes the address: the digest depends on the
voice, the rate and the SSML template, all of which are server concerns. The client asks for "the
audio of this syllable" and gets bytes.

Synthesis is lazy — a clip missing from disk is made on request and cached — so a deploy that has
not run ``warm-audio`` still works, just slower on the first tap.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import CurrentUser
from app.config import get_settings
from app.db.base import SessionDep
from app.db.models.content import Item, ItemType, Kana
from app.logging import get_logger
from app.services import audio_service
from app.speech.keys import Encoding, Ssml
from app.speech.provider import SpeechProvider

log = get_logger(__name__)

router = APIRouter(prefix="/audio", tags=["audio"])

MEDIA_TYPES = {Encoding.mp3: "audio/mpeg", Encoding.ogg: "audio/ogg"}
# A tap should not wait on a network round trip twice. The bytes are immutable — the digest changes
# if anything about them changes — so they can be cached hard.
IMMUTABLE: dict[str, str] = {"Cache-Control": "public, max-age=31536000, immutable"}


def _provider() -> SpeechProvider:
    """Built per request rather than held on the app: this is the only place that needs it, and a
    deployment with no credentials should fail on the first audio tap rather than at boot."""
    from app.speech.google_tts import GoogleTTS

    return GoogleTTS()


@router.get("/kana/{item_id}.{ext}", response_class=FileResponse)
async def kana_audio(
    item_id: uuid.UUID,
    ext: str,
    user: CurrentUser,
    session: SessionDep,
    slow: Annotated[bool, Query(description="The SSML prosody-rate variant")] = False,
) -> FileResponse:
    """The syllable, spoken.

    Sends the ``reading``, never the written form — the rule the whole content design rests on.
    For kana the two are the same string, but routing it through the same field as everything else
    is what stops the first vocabulary item being the place someone sends 日本 and gets にっぽん.
    """
    try:
        encoding = Encoding(ext)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown audio format") from None

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

    return FileResponse(clip.path_for(encoding), media_type=MEDIA_TYPES[encoding], headers=IMMUTABLE)
