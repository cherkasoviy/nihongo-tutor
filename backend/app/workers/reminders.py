"""Reminder cron.

Runs every minute. The plan's rule: "select users whose local time (``AT TIME ZONE``) matches
``reminder_time``, skips if today's session exists or already reminded (Redis key)".

The timezone comparison happens in Python rather than in SQL. The candidate set is tiny (active
learners who set a reminder), and ``zoneinfo`` gets DST transitions right in a way that is far easier
to test than a database-side ``AT TIME ZONE`` expression — the plan's Phase 1 verification asks for
this cron to be parametrized over timezones, and that test is trivial against a pure function.

The Redis key is claimed with ``SET NX`` *before* the message is sent, so two workers racing on the
same tick cannot both deliver it.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.bot import texts_ru
from app.db.base import get_sessionmaker
from app.db.models.learning import Streak
from app.db.models.users import User
from app.domain import clock
from app.logging import get_logger
from app.services import reminder_service

log = get_logger(__name__)


async def reminder_tick(ctx: dict[str, Any]) -> int:
    """Send the day's nudges. Returns how many were actually delivered."""
    bot: Bot | None = ctx.get("bot")
    if bot is None:
        log.debug("reminder tick skipped: no bot configured")
        return 0

    now: dt.datetime = ctx.get("now") or dt.datetime.now(dt.UTC)
    redis = ctx.get("redis")
    sent = 0

    async with get_sessionmaker()() as session:
        candidates = await reminder_service.reminder_candidates(session)
        for user in reminder_service.select_due(candidates, now):
            if await reminder_service.has_session_today(session, user=user, now=now):
                continue

            local_day = clock.local_date(now, user.timezone)
            key = reminder_service.reminder_key(user.id, local_day)
            if redis is not None:
                claimed = await redis.set(key, b"1", ex=reminder_service.REMINDER_KEY_TTL_SECONDS, nx=True)
                if not claimed:
                    continue

            streak = await session.get(Streak, user.id)
            try:
                await bot.send_message(user.tg_user_id, render_reminder(user, streak.current if streak else 0))
            except TelegramAPIError as exc:  # blocked the bot, deleted account, rate limited
                log.warning("reminder failed", tg_user_id=user.tg_user_id, error=str(exc))
                continue
            sent += 1

    if sent:
        log.info("reminders sent", count=sent)
    return sent


def render_reminder(user: User, streak_current: int) -> str:
    """A streak worth protecting is worth mentioning; a streak of one is just pressure."""
    if streak_current > 1:
        return texts_ru.REMINDER_WITH_STREAK.format(
            streak=streak_current,
            streak_word=texts_ru.streak_word(streak_current),
            minutes=user.daily_minutes_target,
        )
    return texts_ru.REMINDER.format(minutes=user.daily_minutes_target)
