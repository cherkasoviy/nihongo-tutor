"""The reminder cron, parametrized over timezones.

The plan's Phase 1 verification asks for exactly this: the same UTC instant must be "reminder time"
for one learner and not for another purely because of where they live, and a DST transition must not
move a learner's reminder off their chosen local hour.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db.models.users import User, UserStatus
from app.domain import clock
from app.services import reminder_service
from app.workers.reminders import render_reminder

# Zones chosen for their awkwardness: a half-hour offset, a southern-hemisphere DST that runs the
# opposite way to the northern one, and a zone that has stopped shifting at all.
ZONES = ["Europe/Berlin", "America/New_York", "Asia/Kolkata", "Australia/Sydney", "Europe/Moscow", "UTC"]


def _user(timezone: str, reminder: dt.time | None = dt.time(19, 30)) -> User:
    user = User(tg_user_id=1, timezone=timezone, reminder_time=reminder)
    user.status = UserStatus.active
    user.daily_minutes_target = 17
    return user


@pytest.mark.parametrize("timezone", ZONES)
def test_reminder_fires_at_the_learners_local_time(timezone: str) -> None:
    """19:30 local is 19:30 local, wherever that happens to be in UTC."""
    user = _user(timezone)
    local_target = dt.datetime(2026, 6, 15, 19, 30, tzinfo=clock.zone(timezone))
    assert reminder_service.is_due(user, local_target.astimezone(dt.UTC))


@pytest.mark.parametrize("timezone", ZONES)
@pytest.mark.parametrize("offset_minutes", [-120, -30, -2, 1, 30, 120])
def test_reminder_does_not_fire_outside_the_window(timezone: str, offset_minutes: int) -> None:
    user = _user(timezone)
    local_target = dt.datetime(2026, 6, 15, 19, 30, tzinfo=clock.zone(timezone))
    at = (local_target + dt.timedelta(minutes=offset_minutes)).astimezone(dt.UTC)
    assert not reminder_service.is_due(user, at)


@pytest.mark.parametrize(
    ("timezone", "day"),
    [
        ("Europe/Berlin", dt.date(2026, 3, 29)),  # spring forward
        ("Europe/Berlin", dt.date(2026, 10, 25)),  # fall back
        ("America/New_York", dt.date(2026, 3, 8)),
        ("America/New_York", dt.date(2026, 11, 1)),
        ("Australia/Sydney", dt.date(2026, 4, 5)),
        ("Australia/Sydney", dt.date(2026, 10, 4)),
    ],
)
def test_reminder_survives_a_dst_transition(timezone: str, day: dt.date) -> None:
    """On the day the clocks move, the reminder still lands at the learner's chosen local hour.

    19:30 exists in every one of these transitions (they all shift around 02:00-03:00), so the only
    thing that changes is the UTC instant behind it — which is precisely what must not be hard-coded.
    """
    user = _user(timezone)
    at = dt.datetime.combine(day, dt.time(19, 30), tzinfo=clock.zone(timezone)).astimezone(dt.UTC)
    assert reminder_service.is_due(user, at)
    assert clock.local_date(at, timezone) == day


def test_a_single_tick_matches_exactly_one_minute() -> None:
    """The cron runs every minute; a wider window would send the same nudge twice."""
    user = _user("Europe/Berlin")
    base = dt.datetime(2026, 6, 15, 19, 30, tzinfo=clock.zone("Europe/Berlin"))
    matches = [
        reminder_service.is_due(user, (base + dt.timedelta(seconds=s)).astimezone(dt.UTC)) for s in range(0, 120, 10)
    ]
    assert matches.count(True) == 6  # the first 60 seconds only


def test_user_without_a_reminder_is_never_due() -> None:
    assert not reminder_service.is_due(_user("UTC", reminder=None), dt.datetime.now(dt.UTC))


def test_select_due_filters_a_mixed_cohort() -> None:
    """One instant, many learners: only those whose local clock reads 19:30 are picked."""
    at = dt.datetime(2026, 6, 15, 17, 30, tzinfo=dt.UTC)  # 19:30 in Berlin, 13:30 in New York
    users = [_user(z) for z in ZONES]
    due = reminder_service.select_due(users, at)
    assert [u.timezone for u in due] == ["Europe/Berlin"]


def test_an_unknown_timezone_falls_back_instead_of_crashing_the_cron() -> None:
    """One bad row must not stop every other learner's reminder."""
    user = _user("Mars/Olympus_Mons")
    at = dt.datetime(2026, 6, 15, 19, 30, tzinfo=dt.UTC)
    assert reminder_service.is_due(user, at)  # treated as UTC


def test_reminder_key_is_per_learner_and_local_day() -> None:
    import uuid

    a, b = uuid.uuid4(), uuid.uuid4()
    day = dt.date(2026, 6, 15)
    assert reminder_service.reminder_key(a, day) != reminder_service.reminder_key(b, day)
    assert reminder_service.reminder_key(a, day) != reminder_service.reminder_key(a, day + dt.timedelta(days=1))


def test_reminder_text_mentions_a_streak_worth_protecting() -> None:
    user = _user("UTC")
    assert "17" in render_reminder(user, 0)
    assert "5" in render_reminder(user, 5)
    # A streak of one is not yet a chain; nagging about it is pressure, not encouragement.
    assert render_reminder(user, 1) == render_reminder(user, 0)
