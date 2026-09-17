"""Speaking in Telegram.

The bot sends OGG_OPUS, which is what the Bot API wants natively for a voice message — the reason
every clip is stored in both encodings rather than only the MP3 the Mini App needs.

Sending is best-effort by design. A lesson that cannot reach Google, or has hit its monthly ceiling,
must still teach: audio is an addition to the card, never a precondition for it.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.logging import get_logger
from app.services import audio_service
from app.speech.keys import Encoding, Ssml
from app.speech.provider import SpeechProvider

log = get_logger(__name__)


@lru_cache(maxsize=1)
def _provider() -> SpeechProvider:
    """Built once, on first use, and *inside* ``send_voice``'s try block.

    Constructing the client is itself a thing that fails — on a machine with no credentials it
    raises ``DefaultCredentialsError``. Taking it as an argument would evaluate it before the
    try, which is how a best-effort helper stops being best-effort: the lesson would die on a
    missing key rather than carrying on in silence. Tests patch this name.
    """
    from app.speech.google_tts import GoogleTTS

    return GoogleTTS()


async def send_voice(
    message: Message,
    session: AsyncSession,
    *,
    settings: Settings,
    text: str,
    caption: str | None = None,
    keyboard: InlineKeyboardMarkup | None = None,
) -> Message | None:
    """Send ``text`` as a voice message. Returns ``None`` if audio was not possible.

    A ``None`` is not an error the caller should propagate — it means "carry on without sound".
    """
    bot: Bot | None = message.bot
    if bot is None or not text:
        return None

    try:
        clip = await audio_service.get_or_create(
            session,
            provider=_provider(),
            text=text,
            voice=settings.tts_voice,
            rate=settings.tts_rate,
            ssml=Ssml.plain,
            audio_dir=settings.audio_dir,
            monthly_char_ceiling=settings.tts_monthly_char_ceiling,
            now=dt.datetime.now(dt.UTC),
        )
    except audio_service.QuotaExceeded as err:
        log.warning("voice skipped: monthly ceiling", text=text, error=str(err))
        return None
    except Exception as err:
        log.warning("voice skipped: synthesis failed", text=text, error=str(err))
        return None

    cached = await audio_service.telegram_file_id(session, digest=clip.hash)
    payload: str | FSInputFile = cached or FSInputFile(Path(clip.path_for(Encoding.ogg)))
    try:
        sent = await message.answer_voice(voice=payload, caption=caption, reply_markup=keyboard)
    except TelegramAPIError as err:
        log.warning("voice not delivered", text=text, error=str(err))
        return None

    if sent.voice is not None:
        await audio_service.remember_telegram_file_id(session, digest=clip.hash, file_id=sent.voice.file_id)
    return sent
