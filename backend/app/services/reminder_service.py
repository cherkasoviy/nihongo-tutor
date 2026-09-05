"""Who should get a nudge on this cron tick.

The cron fires every minute for everyone, so the selection has to be cheap and, more importantly,
exact: a reminder that arrives twice is worse than one that arrives late. Three independent guards
stand between a tick and a message — the learner's local clock has to have just passed their
reminder time, they must not have already finished today, and a Redis key must not already record a
send for that local date.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.learning import LearningSession, SessionOutcome
from app.db.models.users import User, UserStatus
from app.domain import clock

REMINDER_KEY_TTL_SECONDS = 36 * 3600


async def reminder_candidates(session: AsyncSession) -> list[User]:
    """Active learners who asked to be reminded at all."""
    stmt = select(User).where(User.status == UserStatus.active, User.reminder_time.is_not(None))
    return list(await session.scalars(stmt))


def is_due(user: User, now: dt.datetime, *, window_minutes: int = 1) -> bool:
    if user.reminder_time is None:
        return False
    return clock.is_time_due(now, user.timezone, user.reminder_time, window_minutes=window_minutes)


async def has_session_today(session: AsyncSession, *, user: User, now: dt.datetime) -> bool:
    """Did this learner already start today's session? Nudging mid-session would be noise."""
    today = clock.local_date(now, user.timezone)
    stmt = select(LearningSession.id).where(
        LearningSession.user_id == user.id,
        LearningSession.local_date == today,
        LearningSession.outcome != SessionOutcome.abandoned,
    )
    return (await session.scalars(stmt)).first() is not None


def reminder_key(user_id: uuid.UUID, local_day: dt.date) -> str:
    """Redis dedupe key. Keyed on the learner's local date, so a timezone change cannot re-fire it."""
    return f"reminder:{user_id}:{local_day.isoformat()}"


def select_due(users: Sequence[User], now: dt.datetime, *, window_minutes: int = 1) -> list[User]:
    """Pure filter over already-loaded users — the part worth parametrizing over timezones in tests."""
    return [user for user in users if is_due(user, now, window_minutes=window_minutes)]
