"""Adaptive daily planner.

Two decisions live here, both taken before a single step is materialised: how many *new* items the
learner meets today, and how today's minutes are split between the session's sections. Both are
pure functions of a snapshot the service layer assembles, so a plan can be recomputed, replayed in
tests, or explained to the learner without touching the database.

The new-items rule is the one from the plan, kept verbatim down to its thresholds. Its shape is
worth understanding: the learner's time budget, not their appetite, is the scarce resource. A day's
warm-up can absorb only ``0.35 * T / avg_review_s`` reviews, so ``backlog_ratio`` measures how far
the due queue has outgrown that. Introducing new items while the ratio is above 1 is how a review
backlog becomes permanent, which is the single most common way a spaced-repetition habit dies.

Section budgets come back as a mapping of *seconds*, never as a step list. Phase 1 has no audio, so
the kana stage cannot fill its listening slot — that is the caller's problem to solve (spend it on
warm-up, or shorten the session), and returning the full mapping keeps the choice visible instead of
hiding a dropped section inside the planner.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.db.models.content import ItemStage

__all__ = [
    "BACKLOG_PAUSE_RATIO",
    "BACKLOG_TAPER_RATIO",
    "DEFAULT_AVG_REVIEW_SECONDS",
    "DEFAULT_DAILY_MINUTES_TARGET",
    "HIGH_RETENTION_7D",
    "LOW_RETENTION_7D",
    "MAX_BACKLOG_RATIO",
    "NEW_ITEMS_CEILING",
    "SECTION_SHARES",
    "DailyPlanBlueprint",
    "PlannerInput",
    "SessionSection",
    "backlog_ratio",
    "base_new_items",
    "plan_day",
    "review_capacity",
]

DEFAULT_DAILY_MINUTES_TARGET: Final = 17
DEFAULT_AVG_REVIEW_SECONDS: Final = 8.0

KANA_NEW_PER_DAY: Final = 5
CORE_VOCAB_PER_DAY: Final = 4
CORE_GRAMMAR_EVERY_N_DAYS: Final = 2

BACKLOG_PAUSE_RATIO: Final = 1.5
BACKLOG_TAPER_RATIO: Final = 1.0
LOW_RETENTION_7D: Final = 0.80
HIGH_RETENTION_7D: Final = 0.92
LOW_RETENTION_PENALTY: Final = 2
NEW_ITEMS_CEILING: Final = 10
SESSIONS_UNDER_TARGET: Final = 3
MISSED_DAYS_THRESHOLD: Final = 3
MISSED_DAYS_CAP: Final = 2

# A finite stand-in for "the queue is hopeless", so the ratio stays JSON-serialisable on its way
# into ``daily_plans``. Any value above BACKLOG_PAUSE_RATIO produces the same decision anyway.
MAX_BACKLOG_RATIO: Final = 99.0


class SessionSection(enum.StrEnum):
    """The five parts of a session, in the order they are presented."""

    warmup = "warmup"
    new_items = "new_items"
    listening = "listening"
    output = "output"
    wrapup = "wrapup"


SECTION_SHARES: Final[Mapping[str, float]] = {
    SessionSection.warmup: 0.35,
    SessionSection.new_items: 0.25,
    SessionSection.listening: 0.15,
    SessionSection.output: 0.15,
    SessionSection.wrapup: 0.10,
}


@dataclass(frozen=True, slots=True)
class PlannerInput:
    """A learner-day snapshot, assembled by ``plan_service`` before the session starts."""

    stage: ItemStage
    due_count: int
    retention_7d: float | None
    """Recall rate over the last 7 days, or None when there is not enough history to trust it."""
    daily_minutes_target: int = DEFAULT_DAILY_MINUTES_TARGET
    avg_review_seconds: float = DEFAULT_AVG_REVIEW_SECONDS
    missed_days: int = 0
    """Days since the last finished session; only ever ≥3 on the first session back."""
    recent_session_seconds: Sequence[float] = ()
    """Durations of the last finished sessions, most recent first."""
    available_new_items: int = 0
    """Un-introduced items the curriculum can actually offer; the hard ceiling on the answer."""
    day_index: int = 0
    """Days spent in the current stage, for the core stage's every-other-day grammar slot."""


@dataclass(frozen=True, slots=True)
class DailyPlanBlueprint:
    """What the planner decided; stored in ``daily_plans`` and expanded into steps lazily."""

    new_items: int
    review_budget: int
    backlog_ratio: float
    section_seconds: Mapping[str, float]


def review_capacity(daily_minutes_target: int, avg_review_seconds: float) -> float:
    """How many reviews the warm-up can hold, in cards.

    ``daily_minutes_target`` is minutes and ``avg_review_seconds`` is seconds; the conversion is the
    easiest thing to get wrong here, so it happens exactly once, in this function.
    """
    if daily_minutes_target <= 0 or avg_review_seconds <= 0:
        return 0.0
    warmup_seconds = SECTION_SHARES[SessionSection.warmup] * daily_minutes_target * 60.0
    return warmup_seconds / avg_review_seconds


def backlog_ratio(due_count: int, daily_minutes_target: int, avg_review_seconds: float) -> float:
    """Due cards as a multiple of what the warm-up can absorb. 1.0 means exactly full."""
    if due_count <= 0:
        return 0.0
    capacity = review_capacity(daily_minutes_target, avg_review_seconds)
    if capacity <= 0.0:
        return MAX_BACKLOG_RATIO
    return min(due_count / capacity, MAX_BACKLOG_RATIO)


def base_new_items(stage: ItemStage, day_index: int) -> int:
    """The unadjusted daily dose for a stage.

    Core-stage grammar lands on odd ``day_index`` — every second day, starting from the day after
    the learner enters the stage, so the transition day is not the heaviest one.
    """
    if stage is ItemStage.core:
        return CORE_VOCAB_PER_DAY + (1 if day_index % CORE_GRAMMAR_EVERY_N_DAYS == 1 else 0)
    return KANA_NEW_PER_DAY


def _finished_under_target(recent_session_seconds: Sequence[float], target_seconds: float) -> bool:
    """True when the last three sessions all came in under the daily target."""
    if len(recent_session_seconds) < SESSIONS_UNDER_TARGET:
        return False
    return all(seconds < target_seconds for seconds in recent_session_seconds[:SESSIONS_UNDER_TARGET])


def plan_day(inp: PlannerInput) -> DailyPlanBlueprint:
    """Decide today's new-item dose and section budgets."""
    target_seconds = max(0, inp.daily_minutes_target) * 60.0
    capacity = review_capacity(inp.daily_minutes_target, inp.avg_review_seconds)
    ratio = backlog_ratio(inp.due_count, inp.daily_minutes_target, inp.avg_review_seconds)

    new_items = base_new_items(inp.stage, inp.day_index)
    if ratio > BACKLOG_PAUSE_RATIO:
        new_items = 0
    elif ratio > BACKLOG_TAPER_RATIO:
        new_items = max(1, new_items // 3)

    # A zero from the backlog gate is final: both retention nudges have a floor of 1, so applying
    # them here would resurrect an item on precisely the day we decided to introduce none.
    if new_items > 0 and inp.retention_7d is not None:
        if inp.retention_7d < LOW_RETENTION_7D:
            new_items = max(1, new_items - LOW_RETENTION_PENALTY)
        elif inp.retention_7d > HIGH_RETENTION_7D and _finished_under_target(
            inp.recent_session_seconds, target_seconds
        ):
            new_items = min(new_items + 1, NEW_ITEMS_CEILING)

    if inp.missed_days >= MISSED_DAYS_THRESHOLD:
        new_items = min(new_items, MISSED_DAYS_CAP)

    new_items = min(new_items, max(0, inp.available_new_items))

    return DailyPlanBlueprint(
        new_items=new_items,
        review_budget=min(max(0, inp.due_count), int(capacity)),
        backlog_ratio=ratio,
        section_seconds={section: target_seconds * share for section, share in SECTION_SHARES.items()},
    )
