"""Forgiving streak.

The plan's habit loop is deliberately gentle: a learner who misses one day keeps the chain by
spending one automatic freeze per ISO week. Nothing here punishes, and nothing here compares
learners to each other.

Everything is expressed in the learner's *local* dates, which is what makes the streak DST-safe: a
local date is a local date whether that day happened to be 23 or 25 hours long. The module
therefore speaks :class:`datetime.date` and never a timezone or an instant — turning "now" into the
learner's date belongs to the caller, next to the ``users.timezone`` it needs anyway.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from typing import Final

__all__ = [
    "MIN_ACTIVE_MINUTES",
    "MIN_COMPLETION_RATIO",
    "StreakState",
    "iso_week_key",
    "register_activity",
    "session_counts",
]

MIN_COMPLETION_RATIO: Final = 0.60
MIN_ACTIVE_MINUTES: Final = 12
# Freezes do not accumulate: one unused freeze is a buffer for a bad day, a pile of them is a
# licence to disappear for a week and still call it a streak.
MAX_FREEZES: Final = 1


@dataclass(frozen=True, slots=True)
class StreakState:
    """The ``streaks`` row detached from the database."""

    current: int = 0
    longest: int = 0
    last_active_date: dt.date | None = None
    freezes_available: int = 0
    freeze_earned_week: str | None = None
    freeze_used_dates: tuple[dt.date, ...] = ()


def iso_week_key(local_date: dt.date) -> str:
    """``"2026-W03"``.

    ISO weeks, so the freeze refills on Monday for every learner, and the key carries the ISO year
    rather than the calendar one (2027-01-01 belongs to ``2026-W53``).
    """
    iso = local_date.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def session_counts(
    *,
    completed_steps: int,
    planned_steps: int,
    active_seconds: float,
    minutes_target: int = 17,
) -> bool:
    """Did this session earn the day? At least 60% of the planned steps, or at least 12 minutes.

    The twelve minutes are the plan's absolute floor, not a share of ``minutes_target``: a learner
    who spends twelve honest minutes on a heavy backlog has done the day's work even if the planner
    had queued far more steps than that. ``minutes_target`` can only lower the floor, so a learner
    who set a target below twelve minutes can still finish on time.

    A session with nothing planned counts. There is no such thing as failing a day the app had
    nothing to ask about: the learner turned up, everything they had been taught was still resting,
    and the curriculum had nothing new to offer. Treating that as a break would punish being ahead,
    which is the opposite of what a forgiving streak is for — and it is not an edge case while the
    kana stage is all the content there is, because a learner who finishes it has a fortnight of
    such days waiting. The streak still measures turning up, since it only ever advances when the
    learner opens a session themselves.
    """
    if planned_steps == 0:
        return True
    if completed_steps / planned_steps >= MIN_COMPLETION_RATIO:
        return True
    return active_seconds >= min(MIN_ACTIVE_MINUTES, minutes_target) * 60


def register_activity(state: StreakState, local_date: dt.date) -> StreakState:
    """Record that ``local_date`` counted as done, and return the resulting streak.

    A repeat of the last active date is a no-op, as is any older date: a second client finishing
    the same session, or a late job, must not double count or rewrite history.
    """
    last = state.last_active_date
    if last is not None and local_date <= last:
        return state

    earned = _grant_weekly_freeze(state, local_date)
    if last is None:
        return _extend(earned, 1, local_date)

    missed = (local_date - last).days - 1
    if missed == 0:
        return _extend(earned, earned.current + 1, local_date)
    if missed == 1 and earned.freezes_available > 0:
        # The frozen day keeps the chain intact but is not itself credited: only real sessions count.
        spent = replace(
            earned,
            freezes_available=earned.freezes_available - 1,
            freeze_used_dates=(*earned.freeze_used_dates, last + dt.timedelta(days=1)),
        )
        return _extend(spent, spent.current + 1, local_date)
    return _extend(earned, 1, local_date)


def _grant_weekly_freeze(state: StreakState, local_date: dt.date) -> StreakState:
    """Top the learner up to one freeze, once per ISO week.

    ``freeze_earned_week`` is what makes the grant weekly rather than per session.
    """
    week = iso_week_key(local_date)
    if state.freeze_earned_week == week:
        return state
    return replace(
        state,
        freezes_available=min(state.freezes_available + 1, MAX_FREEZES),
        freeze_earned_week=week,
    )


def _extend(state: StreakState, current: int, local_date: dt.date) -> StreakState:
    return replace(state, current=current, longest=max(state.longest, current), last_active_date=local_date)
