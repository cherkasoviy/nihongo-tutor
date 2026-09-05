"""Streak rules, including the DST case Phase 1 verification calls for."""

from __future__ import annotations

import datetime as dt
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest

from app.domain.streak import StreakState, iso_week_key, register_activity, session_counts

DAY = dt.timedelta(days=1)
# A Monday in ISO week 2026-W02, so Mon..Sun below stay inside one freeze week.
MON = dt.date(2026, 1, 5)


def _walk(dates: list[dt.date], state: StreakState | None = None) -> StreakState:
    result = state or StreakState()
    for date in dates:
        result = register_activity(result, date)
    return result


# --- DST -------------------------------------------------------------------------------------

# Windows straddling a real clock change; Europe/Moscow would be useless here, it stopped shifting
# in 2014. The UTC instants are midday locally, so the only thing DST changes is the wall clock.
DST_WINDOWS = [
    ("Europe/Berlin", dt.datetime(2026, 3, 24, 12, tzinfo=dt.UTC)),  # spring forward 2026-03-29
    ("Europe/Berlin", dt.datetime(2026, 10, 20, 12, tzinfo=dt.UTC)),  # fall back 2026-10-25
    ("America/New_York", dt.datetime(2026, 3, 3, 17, tzinfo=dt.UTC)),  # spring forward 2026-03-08
    ("America/New_York", dt.datetime(2026, 10, 27, 17, tzinfo=dt.UTC)),  # fall back 2026-11-01
]


@pytest.mark.parametrize(("zone", "start_utc"), DST_WINDOWS)
def test_streak_survives_dst_because_a_local_date_is_just_a_date(zone: str, start_utc: dt.datetime) -> None:
    """Twelve real instants 24h apart, converted to local dates across a DST boundary.

    One of those days is 23 or 25 hours long, so a streak that measured elapsed time would either
    break or double count. Measuring local dates makes the shift a non-event.
    """
    tz = ZoneInfo(zone)
    local_times = [(start_utc + i * DAY).astimezone(tz) for i in range(12)]

    # The window must actually contain a clock change, otherwise this test proves nothing.
    assert len({t.utcoffset() for t in local_times}) == 2
    dates = [t.date() for t in local_times]
    assert all((b - a).days == 1 for a, b in pairwise(dates))

    state = StreakState()
    for expected, date in enumerate(dates, start=1):
        state = register_activity(state, date)
        assert state.current == expected

    assert state.longest == len(dates)
    assert state.freeze_used_dates == ()  # nothing was missed, so nothing was frozen
    assert state.freezes_available == 1


# --- freezes ---------------------------------------------------------------------------------


def test_one_missed_day_consumes_the_weekly_freeze() -> None:
    state = register_activity(StreakState(), MON)
    assert state.freezes_available == 1

    state = register_activity(state, MON + 2 * DAY)

    assert state.current == 2  # the frozen day bridges the gap, it does not add a day
    assert state.longest == 2
    assert state.freezes_available == 0
    assert state.freeze_used_dates == (MON + DAY,)


def test_two_missed_days_reset_the_streak_and_leave_the_freeze_alone() -> None:
    state = _walk([MON, MON + DAY])

    state = register_activity(state, MON + 4 * DAY)

    assert state.current == 1
    assert state.longest == 2
    assert state.freezes_available == 1  # a freeze covers one day, never a long weekend
    assert state.freeze_used_dates == ()


def test_second_miss_in_the_same_week_resets_because_the_freeze_is_spent() -> None:
    state = _walk([MON, MON + 2 * DAY])
    assert state.freezes_available == 0

    state = register_activity(state, MON + 4 * DAY)

    assert state.current == 1
    assert state.freeze_used_dates == (MON + DAY,)  # still only the first gap was covered


def test_freeze_is_regranted_the_following_iso_week() -> None:
    state = _walk([MON, MON + 2 * DAY, MON + 3 * DAY, MON + 4 * DAY, MON + 5 * DAY, MON + 6 * DAY])
    assert state.freeze_earned_week == iso_week_key(MON) == "2026-W02"
    assert state.freezes_available == 0

    next_monday = MON + 7 * DAY
    state = register_activity(state, next_monday)
    assert state.freeze_earned_week == iso_week_key(next_monday) == "2026-W03"
    assert state.freezes_available == 1

    state = register_activity(state, next_monday + 2 * DAY)  # spend the new one
    assert state.current == 8
    assert state.freezes_available == 0
    assert state.freeze_used_dates == (MON + DAY, next_monday + DAY)


def test_freezes_do_not_accumulate_across_unbroken_weeks() -> None:
    state = _walk([MON + i * DAY for i in range(15)])  # three ISO weeks, nothing missed

    assert state.current == 15
    assert state.freezes_available == 1


# --- bookkeeping -----------------------------------------------------------------------------


def test_registering_the_same_local_date_twice_is_idempotent() -> None:
    once = register_activity(StreakState(), MON)

    assert register_activity(once, MON) == once
    assert register_activity(once, MON - 3 * DAY) == once  # a late job cannot rewrite history


def test_longest_never_decreases() -> None:
    state = _walk([MON + i * DAY for i in range(5)])
    assert (state.current, state.longest) == (5, 5)

    state = register_activity(state, MON + 10 * DAY)  # a week off: the streak restarts
    assert (state.current, state.longest) == (1, 5)

    state = _walk([MON + i * DAY for i in range(11, 15)], state)
    assert (state.current, state.longest) == (5, 5)

    state = register_activity(state, MON + 15 * DAY)
    assert (state.current, state.longest) == (6, 6)


def test_iso_week_key_follows_the_iso_year() -> None:
    assert iso_week_key(dt.date(2026, 1, 1)) == "2026-W01"
    assert iso_week_key(MON) == "2026-W02"
    assert iso_week_key(dt.date(2027, 1, 1)) == "2026-W53"  # calendar 2027, ISO week 53 of 2026


# --- session completion ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("completed", "planned", "seconds", "expected"),
    [
        (6, 10, 0.0, True),  # exactly 60%
        (5, 10, 0.0, False),
        (3, 5, 0.0, True),
        (9, 15, 0.0, True),
        (12, 20, 0.0, True),
        (4, 7, 0.0, False),  # 57%
        (0, 24, 12 * 60, True),  # ran out of steam, but put in the twelve minutes
        (0, 24, 12 * 60 - 1, False),
        (0, 0, 12 * 60, True),  # nothing planned: only time can carry the day
        (0, 0, 0.0, False),
    ],
)
def test_session_counts(completed: int, planned: int, seconds: float, expected: bool) -> None:
    assert session_counts(completed_steps=completed, planned_steps=planned, active_seconds=seconds) is expected


def test_twelve_minute_floor_is_absolute_not_a_share_of_the_target() -> None:
    # A 30-minute target must not raise the floor to 60% of 30 = 18 minutes.
    assert session_counts(completed_steps=0, planned_steps=40, active_seconds=12 * 60, minutes_target=30)


def test_time_floor_never_exceeds_the_learners_own_target() -> None:
    assert session_counts(completed_steps=0, planned_steps=30, active_seconds=10 * 60, minutes_target=10)
    assert not session_counts(completed_steps=0, planned_steps=30, active_seconds=10 * 60)  # default target
