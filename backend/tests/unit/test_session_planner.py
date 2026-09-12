"""Planner tests.

The numbers are chosen so ``review_capacity`` is exactly 60.0 cards: 20 minutes × 0.35 ÷ 7 s. That
makes ``due_count`` readable as a backlog ratio (60 → 1.0, 90 → 1.5) and lets the threshold tests
sit exactly on the boundary instead of near it.
"""

from __future__ import annotations

import pytest

from app.db.models.content import ItemStage
from app.domain.session_planner import (
    BACKLOG_PAUSE_RATIO,
    DEFAULT_AVG_REVIEW_SECONDS,
    DEFAULT_DAILY_MINUTES_TARGET,
    KANA_NEW_PER_DAY,
    LOW_RETENTION_PENALTY,
    MAX_NEW_PER_DAY,
    MISSED_DAYS_CAP,
    NEW_ITEMS_CEILING,
    SECTION_SHARES,
    DailyPlanBlueprint,
    PlannerInput,
    SessionSection,
    backlog_ratio,
    base_new_items,
    plan_day,
    review_capacity,
)

TARGET_MINUTES = 20
AVG_REVIEW_SECONDS = 7.0
CAPACITY = 60  # cards the warm-up holds at the settings above
TARGET_SECONDS = TARGET_MINUTES * 60.0
FAST_SESSION = TARGET_SECONDS - 300.0
SLOW_SESSION = TARGET_SECONDS + 100.0
THREE_FAST_SESSIONS = (FAST_SESSION, FAST_SESSION, FAST_SESSION)
# What the backlog taper leaves of the kana dose: the plan's `max(1, base // 3)`.
TAPERED = max(1, KANA_NEW_PER_DAY // 3)


def _plan(**overrides: object) -> DailyPlanBlueprint:
    params: dict[str, object] = {
        "stage": ItemStage.kana_hira,
        "due_count": 0,
        "retention_7d": None,
        "daily_minutes_target": TARGET_MINUTES,
        "avg_review_seconds": AVG_REVIEW_SECONDS,
        "available_new_items": 100,
    }
    params.update(overrides)
    return plan_day(PlannerInput(**params))  # type: ignore[arg-type]


# --- unit conversion -------------------------------------------------------


def test_review_capacity_converts_minutes_to_seconds() -> None:
    assert review_capacity(TARGET_MINUTES, AVG_REVIEW_SECONDS) == CAPACITY
    assert review_capacity(17, 8.0) == pytest.approx(0.35 * 17 * 60 / 8.0)


def test_backlog_ratio_is_due_over_capacity() -> None:
    assert backlog_ratio(CAPACITY, TARGET_MINUTES, AVG_REVIEW_SECONDS) == 1.0
    assert backlog_ratio(CAPACITY * 2, TARGET_MINUTES, AVG_REVIEW_SECONDS) == 2.0
    assert backlog_ratio(0, TARGET_MINUTES, AVG_REVIEW_SECONDS) == 0.0


# --- base dose -------------------------------------------------------------


@pytest.mark.parametrize("stage", [ItemStage.kana_hira, ItemStage.kana_kata])
def test_kana_stage_introduces_five_a_day(stage: ItemStage) -> None:
    assert _plan(stage=stage).new_items == KANA_NEW_PER_DAY


@pytest.mark.parametrize(("day_index", "expected"), [(0, 4), (1, 5), (2, 4), (3, 5), (4, 4)])
def test_core_stage_adds_grammar_every_second_day(day_index: int, expected: int) -> None:
    assert _plan(stage=ItemStage.core, day_index=day_index).new_items == expected


# --- backlog gate ----------------------------------------------------------


def test_heavy_backlog_stops_new_items() -> None:
    """Phase 1 requirement: a backlog past 1.5× capacity introduces nothing."""
    plan = _plan(due_count=CAPACITY * 3)
    assert plan.backlog_ratio == 3.0
    assert plan.new_items == 0


@pytest.mark.parametrize(
    ("due_count", "expected_ratio", "expected_new"),
    [
        (CAPACITY, 1.0, KANA_NEW_PER_DAY),  # exactly at capacity is not yet a backlog
        (CAPACITY + 1, 61 / 60, TAPERED),  # a single card over tapers to base // 3
        (int(CAPACITY * 1.5), 1.5, TAPERED),  # exactly 1.5 still only tapers
        (int(CAPACITY * 1.5) + 1, 91 / 60, 0),  # a single card past 1.5 stops introductions
    ],
)
def test_backlog_thresholds_are_exclusive(due_count: int, expected_ratio: float, expected_new: int) -> None:
    plan = _plan(due_count=due_count)
    assert plan.backlog_ratio == pytest.approx(expected_ratio)
    assert plan.new_items == expected_new


def test_taper_never_falls_below_one_while_items_remain() -> None:
    plan = _plan(stage=ItemStage.core, day_index=0, due_count=CAPACITY + 1)
    assert plan.new_items == 1  # 4 // 3


# --- retention -------------------------------------------------------------


def test_low_retention_removes_two_items() -> None:
    assert _plan(retention_7d=0.79).new_items == KANA_NEW_PER_DAY - LOW_RETENTION_PENALTY


def test_retention_exactly_at_low_threshold_is_not_penalised() -> None:
    assert _plan(retention_7d=0.80).new_items == KANA_NEW_PER_DAY


def test_low_retention_leaves_at_least_one_item() -> None:
    assert _plan(stage=ItemStage.core, day_index=0, due_count=CAPACITY + 1, retention_7d=0.5).new_items == 1


def test_high_retention_and_three_fast_sessions_adds_one() -> None:
    """Phase 1 requirement: a comfortable learner gets more."""
    plan = _plan(retention_7d=0.95, recent_session_seconds=THREE_FAST_SESSIONS)
    assert plan.new_items == KANA_NEW_PER_DAY + 1


def test_retention_exactly_at_high_threshold_does_not_bump() -> None:
    assert _plan(retention_7d=0.92, recent_session_seconds=THREE_FAST_SESSIONS).new_items == KANA_NEW_PER_DAY


@pytest.mark.parametrize(
    "recent",
    [
        (),
        (FAST_SESSION,),
        (FAST_SESSION, FAST_SESSION),  # fewer than three sessions is not enough evidence
        (SLOW_SESSION, FAST_SESSION, FAST_SESSION),
        (FAST_SESSION, FAST_SESSION, SLOW_SESSION),
        (FAST_SESSION, FAST_SESSION, TARGET_SECONDS),  # "under T" is strict
    ],
)
def test_high_retention_bump_needs_three_fast_sessions(recent: tuple[float, ...]) -> None:
    assert _plan(retention_7d=0.95, recent_session_seconds=recent).new_items == KANA_NEW_PER_DAY


def test_only_the_three_most_recent_sessions_count() -> None:
    recent = (*THREE_FAST_SESSIONS, SLOW_SESSION)
    assert _plan(retention_7d=0.95, recent_session_seconds=recent).new_items == KANA_NEW_PER_DAY + 1


def test_unknown_retention_makes_no_adjustment() -> None:
    assert _plan(retention_7d=None, recent_session_seconds=THREE_FAST_SESSIONS).new_items == KANA_NEW_PER_DAY


@pytest.mark.parametrize("retention", [0.10, 0.99])
def test_retention_never_resurrects_a_backlog_zero(retention: float) -> None:
    """Both nudges have a floor of 1; neither may undo the backlog gate's hard stop."""
    plan = _plan(due_count=CAPACITY * 3, retention_7d=retention, recent_session_seconds=THREE_FAST_SESSIONS)
    assert plan.new_items == 0


# --- return from an absence ------------------------------------------------


def test_three_missed_days_caps_the_first_session_back() -> None:
    """Phase 1 requirement: coming back after 3 days is a gentle day."""
    plan = _plan(missed_days=3, retention_7d=0.95, recent_session_seconds=THREE_FAST_SESSIONS)
    assert plan.new_items == 2


@pytest.mark.parametrize(
    ("missed_days", "expected"),
    [
        (0, KANA_NEW_PER_DAY),
        (1, KANA_NEW_PER_DAY),
        (2, KANA_NEW_PER_DAY),
        (3, MISSED_DAYS_CAP),
        (10, MISSED_DAYS_CAP),
    ],
)
def test_missed_day_cap_threshold(missed_days: int, expected: int) -> None:
    assert _plan(missed_days=missed_days).new_items == expected


def test_missed_day_cap_does_not_raise_a_backlog_zero() -> None:
    assert _plan(due_count=CAPACITY * 3, missed_days=5).new_items == 0


# --- availability clamp ----------------------------------------------------


@pytest.mark.parametrize(
    ("available", "expected"),
    [(0, 0), (2, 2), (5, 5), (50, KANA_NEW_PER_DAY), (-1, 0)],
)
def test_new_items_never_exceed_what_the_curriculum_has_left(available: int, expected: int) -> None:
    assert _plan(available_new_items=available).new_items == expected


def test_availability_clamps_the_high_retention_bump_too() -> None:
    plan = _plan(retention_7d=0.95, recent_session_seconds=THREE_FAST_SESSIONS, available_new_items=5)
    assert plan.new_items == 5


def test_new_items_stay_under_the_ceiling() -> None:
    assert _plan(retention_7d=0.95, recent_session_seconds=THREE_FAST_SESSIONS).new_items <= NEW_ITEMS_CEILING


# --- review budget ---------------------------------------------------------


@pytest.mark.parametrize(("due_count", "expected"), [(0, 0), (10, 10), (CAPACITY, CAPACITY), (500, CAPACITY)])
def test_review_budget_is_the_smaller_of_due_and_capacity(due_count: int, expected: int) -> None:
    assert _plan(due_count=due_count).review_budget == expected


# --- section budgets -------------------------------------------------------


def test_section_budgets_split_the_target() -> None:
    sections = _plan(daily_minutes_target=17).section_seconds
    assert sections[SessionSection.warmup] == pytest.approx(357.0)
    assert sections[SessionSection.new_items] == pytest.approx(255.0)
    assert sections[SessionSection.listening] == pytest.approx(153.0)
    assert sections[SessionSection.output] == pytest.approx(153.0)
    assert sections[SessionSection.wrapup] == pytest.approx(102.0)
    assert sum(sections.values()) == pytest.approx(17 * 60.0)


def test_kana_stage_still_reports_a_listening_budget() -> None:
    """Phase 1 has no kana audio, but the planner must not silently drop the section."""
    sections = _plan(stage=ItemStage.kana_hira).section_seconds
    assert set(sections) == {section.value for section in SessionSection}
    assert sections[SessionSection.listening] > 0


# --- degenerate inputs -----------------------------------------------------


@pytest.mark.parametrize(("minutes", "avg_seconds"), [(0, AVG_REVIEW_SECONDS), (-5, AVG_REVIEW_SECONDS), (20, 0.0)])
def test_no_capacity_is_a_maximal_backlog_not_a_crash(minutes: int, avg_seconds: float) -> None:
    plan = _plan(daily_minutes_target=minutes, avg_review_seconds=avg_seconds, due_count=1)
    assert plan.backlog_ratio > 1.5
    assert plan.new_items == 0
    assert plan.review_budget == 0


def test_no_capacity_and_no_due_cards_is_a_zero_ratio() -> None:
    plan = _plan(daily_minutes_target=0, due_count=0)
    assert plan.backlog_ratio == 0.0
    assert plan.new_items == KANA_NEW_PER_DAY  # nothing due, so nothing holds introductions back
    assert all(seconds == 0.0 for seconds in plan.section_seconds.values())


def test_backlog_ratio_is_finite_so_it_can_be_stored() -> None:
    plan = _plan(daily_minutes_target=0, due_count=10_000)
    assert plan.backlog_ratio < float("inf")


def test_blueprint_is_immutable() -> None:
    plan = _plan()
    with pytest.raises(AttributeError):
        plan.new_items = 99  # type: ignore[misc]


# --- the learner's own pace -------------------------------------------------
# The stage default is a starting point, not a verdict. A complete beginner with a free afternoon
# and a learner returning to a backlog need different numbers, and only one of them is the app's
# business to decide.


def test_the_kana_default_matches_the_plans_own_time_budget() -> None:
    """25% of a 17-minute session at 8 s a step is about ten introductions, not five.

    Five came from vocabulary pacing, where an item carries meaning, a reading and a production
    form. A kana is a shape and a sound.
    """
    seconds = SECTION_SHARES[SessionSection.new_items] * DEFAULT_DAILY_MINUTES_TARGET * 60
    per_item = 3 * DEFAULT_AVG_REVIEW_SECONDS  # intro, immediate check, production drill
    assert 8 <= seconds / per_item <= 12
    assert base_new_items(ItemStage.kana_hira, day_index=0) == 10


@pytest.mark.parametrize("chosen", [1, 5, 10, 15, 20])
def test_a_chosen_pace_replaces_the_stage_default(chosen: int) -> None:
    assert base_new_items(ItemStage.kana_hira, day_index=0, chosen=chosen) == chosen


@pytest.mark.parametrize(("chosen", "expected"), [(0, 1), (-5, 1), (50, MAX_NEW_PER_DAY)])
def test_a_chosen_pace_is_clamped_to_something_survivable(chosen: int, expected: int) -> None:
    assert base_new_items(ItemStage.kana_hira, day_index=0, chosen=chosen) == expected


def test_a_chosen_pace_cannot_outrun_the_backlog_gate() -> None:
    """The safety rail is the gate, not a low default. Asking for twenty on a bad day gets nothing."""
    drowning = PlannerInput(
        stage=ItemStage.kana_hira,
        due_count=500,
        retention_7d=None,
        available_new_items=200,
        new_items_target=MAX_NEW_PER_DAY,
    )
    assert plan_day(drowning).backlog_ratio > BACKLOG_PAUSE_RATIO
    assert plan_day(drowning).new_items == 0


def test_a_chosen_pace_still_tapers_on_weak_recall() -> None:
    struggling = PlannerInput(
        stage=ItemStage.kana_hira,
        due_count=0,
        retention_7d=0.5,
        available_new_items=200,
        new_items_target=12,
    )
    assert plan_day(struggling).new_items == 12 - LOW_RETENTION_PENALTY


def test_a_chosen_pace_is_still_capped_by_what_the_curriculum_has_left() -> None:
    nearly_done = PlannerInput(
        stage=ItemStage.kana_kata,
        due_count=0,
        retention_7d=None,
        available_new_items=3,
        new_items_target=MAX_NEW_PER_DAY,
    )
    assert plan_day(nearly_done).new_items == 3
