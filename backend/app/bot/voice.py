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
    reply: bool = False,
) -> Message | None:
    """Send ``text`` as a voice message. Returns ``None`` if audio was not possible.

    A ``None`` is not an error the caller should propagate — it means "carry on without sound".

    ``reply`` quotes the message it answers. Use it for a clip that follows a card — the quote is
    what ties a sound to the syllable it belongs to when the learner scrolls back. The card's own
    voice does not reply to anything: it *is* the card.
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

    bytes_on_disk = FSInputFile(Path(clip.path_for(Encoding.ogg)))
    cached = await audio_service.telegram_file_id(session, digest=clip.hash)

    sent: Message | None = None
    if cached is not None:
        sent = await _try_send(message, cached, caption, keyboard, text=text, why="cached id", reply=reply)
        if sent is None:
            # A rejected id must not silence this clip forever. It is per-bot, and the documented
            # cutover restores a pg_dump into a *different* bot — so every id the dev bot minted
            # arrives at the prod bot, dead on arrival. Without this retry each of those clips
            # would fall back to text on every send, indistinguishable in the logs from a one-off
            # Telegram hiccup.
            log.info("cached telegram file id rejected, re-uploading", hash=clip.hash)
            sent = await _try_send(message, bytes_on_disk, caption, keyboard, text=text, why="re-upload", reply=reply)
    else:
        sent = await _try_send(message, bytes_on_disk, caption, keyboard, text=text, why="first upload", reply=reply)

    if sent is None:
        return None
    if sent.voice is not None and sent.voice.file_id != cached:
        # Overwrites the dead id rather than merely adding one, so the row heals.
        await audio_service.remember_telegram_file_id(session, digest=clip.hash, file_id=sent.voice.file_id)
    return sent


async def _try_send(
    message: Message,
    payload: str | FSInputFile,
    caption: str | None,
    keyboard: InlineKeyboardMarkup | None,
    *,
    text: str,
    why: str,
    reply: bool = False,
) -> Message | None:
    send = message.reply_voice if reply else message.answer_voice
    try:
        return await send(voice=payload, caption=caption, reply_markup=keyboard)
    except TelegramAPIError as err:
        log.warning("voice not delivered", text=text, attempt=why, error=str(err))
        return None
