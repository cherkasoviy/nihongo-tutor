"""Daily watch on the TTS free tier, delivered to Telegram.

Google's own budget alert cannot warn about this. A budget is denominated in money, and the free
tier costs nothing — so by the time a dollar threshold trips, the free allowance is already gone
and the meter is running. The unit that actually matters is *characters*, and the only place that
number exists before the bill does is our own ledger.

So this is not a replacement for the budget alert, it is the half Google cannot do:

* ours fires on characters, before any money is spent, and reaches a phone the owner already checks
* theirs fires on money, across the whole project, and catches anything this app does not do itself

Keep both. They fail differently, which is the point.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Final

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.bot import texts_ru
from app.config import get_settings
from app.db.base import get_sessionmaker
from app.logging import get_logger
from app.services import audio_service

log = get_logger(__name__)

# The free allowance for Neural2 voices, from Google's pricing page: 0 to 1,000,000 characters per
# month, then US$16 per million. Standard and WaveNet get 4,000,000 — if the voice ever changes,
# this has to change with it.
FREE_TIER_CHARS: Final = 1_000_000
WARN_AT: Final = 0.5
ALARM_AT: Final = 0.9


def band(used: int, free_tier: int = FREE_TIER_CHARS) -> str | None:
    """Which threshold this crosses, or ``None`` while there is nothing to say.

    Silence is the normal case and worth protecting: an alert that arrives every day is one nobody
    reads on the day it matters.
    """
    share = used / free_tier
    if share >= 1.0:
        return "over"
    if share >= ALARM_AT:
        return "alarm"
    if share >= WARN_AT:
        return "warn"
    return None


async def quota_tick(ctx: dict[str, Any]) -> int:
    """Check the month's character spend and message the admins if it has moved into a band."""
    bot: Bot | None = ctx.get("bot")
    settings = get_settings()
    if bot is None or not settings.admin_tg_ids:
        log.debug("quota tick skipped", has_bot=bot is not None, admins=len(settings.admin_tg_ids))
        return 0

    now = dt.datetime.now(dt.UTC)
    async with get_sessionmaker()() as session:
        used = await audio_service.chars_this_month(session, now=now)

    level = band(used)
    if level is None:
        log.info("tts quota ok", used=used, free_tier=FREE_TIER_CHARS)
        return 0

    text = texts_ru.TTS_QUOTA_ALERT.format(
        used=f"{used:,}".replace(",", " "),
        free_tier=f"{FREE_TIER_CHARS:,}".replace(",", " "),
        percent=round(100 * used / FREE_TIER_CHARS),
        ceiling=f"{settings.tts_monthly_char_ceiling:,}".replace(",", " "),
        note=texts_ru.TTS_QUOTA_NOTE[level],
    )

    sent = 0
    for tg_id in settings.admin_tg_ids:
        try:
            await bot.send_message(tg_id, text)
            sent += 1
        except TelegramAPIError as err:  # one blocked admin must not silence the rest
            log.warning("quota alert not delivered", tg_id=tg_id, error=str(err))
    log.info("tts quota alert sent", used=used, level=level, recipients=sent)
    return sent
