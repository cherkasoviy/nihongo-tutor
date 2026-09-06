"""Turning instants into the learner's own day.

Every learner-facing notion of time in this app — "today's session", the streak, the reminder — is
local to the learner, not to the server. Centralising the conversion here keeps ``zoneinfo`` out of
the services and makes the timezone edge cases (DST gaps, folds, a bad IANA name in the database)
testable in one place.
"""

from __future__ import annotations

import datetime as dt
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FALLBACK_TIMEZONE: Final = "UTC"


def zone(timezone: str) -> ZoneInfo:
    """Resolve an IANA name, falling back to UTC rather than crashing a cron over one bad row."""
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(FALLBACK_TIMEZONE)


def local_now(now: dt.datetime, timezone: str) -> dt.datetime:
    return now.astimezone(zone(timezone))


def local_date(now: dt.datetime, timezone: str) -> dt.date:
    """The learner's calendar date. This is the unit the streak counts in."""
    return local_now(now, timezone).date()


def day_bounds(local_day: dt.date, timezone: str) -> tuple[dt.datetime, dt.datetime]:
    """The UTC instants that bracket a local day.

    On a spring-forward day midnight may not exist in that zone; ``fold`` handling in ``zoneinfo``
    resolves it to the same instant either way, and the half-open range stays correct because the
    end is computed from the *next* local date rather than by adding 24 hours.
    """
    tz = zone(timezone)
    start = dt.datetime.combine(local_day, dt.time.min, tzinfo=tz)
    end = dt.datetime.combine(local_day + dt.timedelta(days=1), dt.time.min, tzinfo=tz)
    return start.astimezone(dt.UTC), end.astimezone(dt.UTC)


def is_time_due(now: dt.datetime, timezone: str, target: dt.time, *, window_minutes: int = 1) -> bool:
    """Has the learner's local clock just passed ``target``?

    The cron ticks every minute, so the window is a minute wide. Comparing to the minute (rather
    than to an instant) is what keeps a reminder from being missed when a tick is a few seconds late,
    and the window is half-open so a single tick can never fire the same reminder twice.
    """
    local = local_now(now, timezone)
    minutes_now = local.hour * 60 + local.minute
    minutes_target = target.hour * 60 + target.minute
    delta = (minutes_now - minutes_target) % (24 * 60)
    return delta < max(1, window_minutes)
