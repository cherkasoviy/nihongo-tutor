"""Property tests for the FSRS wrapper.

The scheduler is third-party maths we do not own, so these tests pin the handful of properties the
session engine actually relies on — "Again never rewards a lapse", "retrievability only decays",
"a card's due date respects the rating order" — rather than the library's exact numbers, which move
between FSRS releases and again once the per-user optimizer re-fits parameters.

Every scheduler here is built with ``enable_fuzzing=False``: fuzz spreads same-day due dates by a
few percent, which would make the rating-ordering and retention properties flaky for no benefit.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from zoneinfo import ZoneInfo

import pytest

from app.db.models.learning import CardState
from app.domain import srs

NOW = dt.datetime(2026, 1, 1, 9, 0, tzinfo=dt.UTC)

# (state, step) pairs a persisted card can legitimately hold: ``step`` indexes the scheduler's
# learning/relearning steps and is None once the card graduates to review.
GRADED_STATES: list[tuple[CardState, int | None]] = [
    (CardState.learning, 0),
    (CardState.learning, 1),
    (CardState.review, None),
    (CardState.relearning, 0),
]

# Days, spanning a first-session card through a card parked for a decade.
STABILITIES = [0.05, 0.5, 1.0, 2.5, 21.0, 365.0, 3650.0]
DIFFICULTIES = [1.0, 2.5, 5.0, 7.5, 10.0]
ELAPSED_DAYS = [0, 1, 3, 10, 60, 400]

# Sampled geometrically: a uniform grid would spend all its points on one scale, and the forgetting
# curve has to hold from the ten-minute learning step out to a card parked for a decade.
DECAY_SAMPLES = [
    *(dt.timedelta(minutes=m) for m in (0, 1, 5, 10, 30)),
    *(dt.timedelta(hours=h) for h in (1, 2, 4, 8, 16)),
    *(dt.timedelta(days=d) for d in (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 400, 800, 1600, 3650)),
]


@pytest.fixture
def scheduler() -> srs.Scheduler:
    return srs.make_scheduler(enable_fuzzing=False)


def graded_state(
    card_state: CardState,
    step: int | None,
    *,
    stability: float,
    difficulty: float,
    elapsed_days: int,
) -> srs.SrsState:
    """A card that has been reviewed before, positioned ``elapsed_days`` after that review."""
    last_review = NOW - dt.timedelta(days=elapsed_days)
    return srs.SrsState(
        state=card_state,
        step=step,
        stability=stability,
        difficulty=difficulty,
        due=last_review + dt.timedelta(days=1),
        last_review=last_review,
        reps=3,
    )


def walk(scheduler: srs.Scheduler, ratings: list[srs.Rating]) -> Iterator[srs.SrsState]:
    """Grade a fresh card repeatedly, each review happening exactly when the previous fell due."""
    state = srs.new_state(NOW)
    at = state.due
    for rating in ratings:
        state = srs.review(state, rating, at, scheduler=scheduler).state
        at = state.due
        yield state


def mature(scheduler: srs.Scheduler, *, rounds: int = 4) -> srs.SrsState:
    """A card driven to the review state by honest Good answers."""
    final = list(walk(scheduler, [srs.Rating.Good] * rounds))[-1]
    assert final.state is CardState.review
    return final


# ---------------------------------------------------------------------------
# Again never raises stability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("card_state", "step"), GRADED_STATES, ids=lambda p: str(p))
@pytest.mark.parametrize("stability", STABILITIES)
def test_again_never_raises_stability(
    scheduler: srs.Scheduler,
    card_state: CardState,
    step: int | None,
    stability: float,
) -> None:
    """Forgetting must never be rewarded: the plan's headline FSRS invariant."""
    for difficulty in DIFFICULTIES:
        for elapsed_days in ELAPSED_DAYS:
            before = graded_state(
                card_state, step, stability=stability, difficulty=difficulty, elapsed_days=elapsed_days
            )
            after = srs.review(before, srs.Rating.Again, NOW, scheduler=scheduler).state
            assert after.stability is not None
            assert after.stability <= stability, (card_state, difficulty, elapsed_days, after.stability)


@pytest.mark.parametrize("good_reviews", range(1, 8))
def test_again_never_raises_stability_over_an_organic_history(
    scheduler: srs.Scheduler,
    good_reviews: int,
) -> None:
    """The same property on states the scheduler itself produced, not synthetic ones."""
    state = list(walk(scheduler, [srs.Rating.Good] * good_reviews))[-1]
    assert state.stability is not None
    lapsed = srs.review(state, srs.Rating.Again, state.due, scheduler=scheduler).state
    assert lapsed.stability is not None
    assert lapsed.stability <= state.stability


@pytest.mark.parametrize(("card_state", "step"), GRADED_STATES, ids=lambda p: str(p))
def test_again_never_leaves_a_card_easier_than_good_would(
    scheduler: srs.Scheduler,
    card_state: CardState,
    step: int | None,
) -> None:
    """Difficulty is bounded to 1-10 and a lapse never lands below what a Good answer would.

    (FSRS 6 reverts difficulty toward its initial value, so Again on an already-maximal card can
    nudge it *down* a hair — hence the comparison against Good rather than against the old value.)
    """
    for difficulty in DIFFICULTIES:
        before = graded_state(card_state, step, stability=5.0, difficulty=difficulty, elapsed_days=1)
        lapsed = srs.review(before, srs.Rating.Again, NOW, scheduler=scheduler).state
        passed = srs.review(before, srs.Rating.Good, NOW, scheduler=scheduler).state
        assert lapsed.difficulty is not None and passed.difficulty is not None
        assert 1.0 <= lapsed.difficulty <= 10.0
        assert lapsed.difficulty >= passed.difficulty


# ---------------------------------------------------------------------------
# Retrievability
# ---------------------------------------------------------------------------


def test_new_card_has_zero_retrievability_and_sorts_first_in_a_warm_up(scheduler: srs.Scheduler) -> None:
    """The warm-up is ordered by retrievability ascending, so an un-introduced card leads it."""
    fresh = srs.new_state(NOW)
    assert srs.retrievability(fresh, NOW, scheduler=scheduler) == 0.0
    assert srs.retrievability(fresh, NOW + dt.timedelta(days=365), scheduler=scheduler) == 0.0

    seen_today = mature(scheduler)
    forgotten = graded_state(CardState.review, None, stability=2.0, difficulty=8.0, elapsed_days=30)
    warm_up = [seen_today, forgotten, fresh]
    warm_up.sort(key=lambda card: srs.retrievability(card, NOW, scheduler=scheduler))
    assert warm_up[0] is fresh
    assert all(srs.retrievability(card, NOW, scheduler=scheduler) > 0.0 for card in warm_up[1:])


@pytest.mark.parametrize(("card_state", "step"), GRADED_STATES, ids=lambda p: str(p))
@pytest.mark.parametrize("stability", [0.1, 1.0, 20.0, 400.0])
def test_retrievability_decays_monotonically(
    scheduler: srs.Scheduler,
    card_state: CardState,
    step: int | None,
    stability: float,
) -> None:
    """Memory only ever fades between reviews; it must also stay a probability."""
    for difficulty in (1.0, 5.0, 10.0):
        state = graded_state(card_state, step, stability=stability, difficulty=difficulty, elapsed_days=0)
        assert state.last_review is not None
        previous = 1.0 + 1e-9
        for elapsed in DECAY_SAMPLES:
            value = srs.retrievability(state, state.last_review + elapsed, scheduler=scheduler)
            assert 0.0 <= value <= 1.0
            assert value <= previous, (card_state, stability, difficulty, elapsed)
            previous = value


@pytest.mark.parametrize("desired_retention", [0.85, 0.87, 0.90, 0.92])
def test_retrievability_at_the_due_date_is_the_desired_retention(desired_retention: float) -> None:
    """This is the contract of desired_retention: the due date is when recall falls to it.

    Tolerance is loose because due dates are rounded to whole days for review-state cards.
    """
    scheduler = srs.make_scheduler(desired_retention=desired_retention, enable_fuzzing=False)
    state = mature(scheduler, rounds=5)
    assert srs.retrievability(state, state.due, scheduler=scheduler) == pytest.approx(desired_retention, abs=0.01)


# ---------------------------------------------------------------------------
# CardState transitions and counters
# ---------------------------------------------------------------------------


def test_repeated_good_walks_new_to_learning_to_review(scheduler: srs.Scheduler) -> None:
    """``new`` exists only on our side; one Good hands the card to the learning steps, the next
    graduates it, and review is absorbing under Good."""
    fresh = srs.new_state(NOW)
    assert fresh.state is CardState.new
    assert fresh.is_new

    states = list(walk(scheduler, [srs.Rating.Good] * 5))
    assert [s.state for s in states] == [
        CardState.learning,
        CardState.review,
        CardState.review,
        CardState.review,
        CardState.review,
    ]
    assert states[0].step == 1
    assert states[1].step is None  # graduated cards carry no learning step
    assert all(s.lapses == 0 for s in states)
    assert [s.reps for s in states] == [1, 2, 3, 4, 5]
    # Intervals expand as stability grows.
    intervals = [s.interval() for s in states[1:]]
    assert intervals == sorted(intervals)


def test_again_from_review_yields_relearning_and_exactly_one_lapse(scheduler: srs.Scheduler) -> None:
    """A lapse is counted once, on the transition out of review — not again while relearning."""
    reviewed = mature(scheduler)
    assert reviewed.lapses == 0

    lapsed = srs.review(reviewed, srs.Rating.Again, reviewed.due, scheduler=scheduler).state
    assert lapsed.state is CardState.relearning
    assert lapsed.lapses == 1

    again = srs.review(lapsed, srs.Rating.Again, lapsed.due, scheduler=scheduler).state
    assert again.state is CardState.relearning
    assert again.lapses == 1

    recovered = srs.review(again, srs.Rating.Good, again.due, scheduler=scheduler).state
    assert recovered.state is CardState.review
    assert recovered.lapses == 1


@pytest.mark.parametrize("first_rating", [srs.Rating.Again, srs.Rating.Hard, srs.Rating.Good])
def test_again_while_still_learning_does_not_count_a_lapse(
    scheduler: srs.Scheduler,
    first_rating: srs.Rating,
) -> None:
    """Fumbling a card that never reached review is normal acquisition, not forgetting."""
    learning = srs.review(srs.new_state(NOW), first_rating, NOW, scheduler=scheduler).state
    assert learning.state is CardState.learning

    result = srs.review(learning, srs.Rating.Again, NOW + dt.timedelta(minutes=2), scheduler=scheduler)
    assert result.state_before is CardState.learning
    assert result.state.state is CardState.learning
    assert result.state.lapses == 0


@pytest.mark.parametrize("rating", list(srs.Rating))
def test_reps_increment_on_every_review_whatever_the_rating(
    scheduler: srs.Scheduler,
    rating: srs.Rating,
) -> None:
    state = srs.new_state(NOW)
    for expected in range(1, 6):
        result = srs.review(state, rating, state.due, scheduler=scheduler)
        state = result.state
        assert state.reps == expected
        assert result.rating is rating
        assert result.review_at == result.state.last_review


# ---------------------------------------------------------------------------
# Scheduler construction and rating ordering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (0.0, srs.MIN_DESIRED_RETENTION),
        (0.50, srs.MIN_DESIRED_RETENTION),
        (0.84, srs.MIN_DESIRED_RETENTION),
        (0.85, 0.85),
        (0.90, 0.90),
        (0.92, 0.92),
        (0.95, srs.MAX_DESIRED_RETENTION),
        (1.00, srs.MAX_DESIRED_RETENTION),
    ],
)
def test_desired_retention_is_clamped_into_the_plan_band(requested: float, expected: float) -> None:
    """The plan fixes 0.85-0.92; a bad settings value must not produce an absurd review load."""
    assert srs.make_scheduler(desired_retention=requested).desired_retention == pytest.approx(expected)
    assert srs.MIN_DESIRED_RETENTION <= srs.DEFAULT_DESIRED_RETENTION <= srs.MAX_DESIRED_RETENTION
    assert srs.make_scheduler().desired_retention == pytest.approx(srs.DEFAULT_DESIRED_RETENTION)


def assert_rating_order(scheduler: srs.Scheduler, before: srs.SrsState) -> None:
    due = {rating: srs.review(before, rating, NOW, scheduler=scheduler).state.due for rating in srs.Rating}
    assert due[srs.Rating.Again] <= due[srs.Rating.Hard] <= due[srs.Rating.Good] <= due[srs.Rating.Easy], (before, due)


def test_ratings_schedule_in_order_for_a_brand_new_card(scheduler: srs.Scheduler) -> None:
    assert_rating_order(scheduler, srs.new_state(NOW))


@pytest.mark.parametrize(("card_state", "step"), GRADED_STATES, ids=lambda p: str(p))
def test_easy_schedules_no_earlier_than_good_and_good_no_earlier_than_hard(
    scheduler: srs.Scheduler,
    card_state: CardState,
    step: int | None,
) -> None:
    """A better answer never brings a card back sooner, from any starting state."""
    for stability in (0.5, 2.5, 21.0, 365.0):
        for difficulty in (1.0, 5.0, 10.0):
            for elapsed_days in (0, 2, 30):
                assert_rating_order(
                    scheduler,
                    graded_state(
                        card_state, step, stability=stability, difficulty=difficulty, elapsed_days=elapsed_days
                    ),
                )


# ---------------------------------------------------------------------------
# with_due
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shift_days", [-3, 0, 1, 14])
def test_with_due_moves_only_the_due_date(scheduler: srs.Scheduler, shift_days: int) -> None:
    """The planner staggers an item's production/listening siblings; that must not touch memory."""
    state = mature(scheduler)
    moved = srs.with_due(state, state.due + dt.timedelta(days=shift_days))

    assert moved.due == state.due + dt.timedelta(days=shift_days)
    assert (moved.stability, moved.difficulty, moved.state, moved.step) == (
        state.stability,
        state.difficulty,
        state.state,
        state.step,
    )
    assert (moved.reps, moved.lapses, moved.last_review) == (state.reps, state.lapses, state.last_review)
    assert state.due == mature(scheduler).due  # the original is untouched


# --- timezone normalisation -------------------------------------------------
# fsrs rejects any datetime whose tzinfo is not `datetime.timezone.utc` *by identity*, so a correct
# ZoneInfo("UTC") raises just as loudly as a local time. This app converts to the learner's zone for
# the streak, the reminder and "today", so those instants must not become a landmine at the boundary.


@pytest.mark.parametrize(
    "timezone",
    [dt.UTC, ZoneInfo("UTC"), ZoneInfo("Europe/Berlin"), ZoneInfo("Asia/Tokyo"), dt.timezone(dt.timedelta(hours=-5))],
)
def test_any_aware_datetime_is_accepted_whatever_its_tzinfo(timezone: dt.tzinfo) -> None:
    scheduler = srs.make_scheduler(enable_fuzzing=False)
    instant = dt.datetime(2026, 1, 2, 12, 0, tzinfo=dt.UTC)
    state = srs.new_state(instant)

    result = srs.review(state, srs.Rating.Good, instant.astimezone(timezone), scheduler=scheduler)

    # Same instant expressed differently must schedule identically, not merely avoid crashing.
    reference = srs.review(state, srs.Rating.Good, instant, scheduler=scheduler)
    assert result.state.due == reference.state.due
    assert result.state.stability == reference.state.stability


def test_a_naive_datetime_is_refused_rather_than_assumed_to_be_utc() -> None:
    """Guessing would silently shift a review by the learner's offset — a wrong interval is much
    harder to notice than an exception."""
    scheduler = srs.make_scheduler(enable_fuzzing=False)
    state = srs.new_state(dt.datetime(2026, 1, 2, 12, 0, tzinfo=dt.UTC))

    with pytest.raises(ValueError, match="timezone-aware"):
        srs.review(state, srs.Rating.Good, dt.datetime(2026, 1, 3, 12, 0), scheduler=scheduler)


def test_retrievability_accepts_a_local_instant_too() -> None:
    scheduler = srs.make_scheduler(enable_fuzzing=False)
    now = dt.datetime(2026, 1, 2, 12, 0, tzinfo=dt.UTC)
    state = srs.review(srs.new_state(now), srs.Rating.Good, now, scheduler=scheduler).state

    local = now.astimezone(ZoneInfo("Asia/Tokyo"))
    assert srs.retrievability(state, local, scheduler=scheduler) == srs.retrievability(state, now, scheduler=scheduler)


# --- the optimizer's input --------------------------------------------------
# elapsed_days and scheduled_days are what card_service writes into review_logs, and they are the
# Phase 4 FSRS optimizer's whole input. Nothing else asserts them, and they are easy to cross-wire.


def test_review_reports_the_interval_that_was_actually_waited() -> None:
    scheduler = srs.make_scheduler(enable_fuzzing=False)
    start = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)

    first = srs.review(srs.new_state(start), srs.Rating.Good, start, scheduler=scheduler)
    assert first.elapsed_days == 0, "a card reviewed the day it was created has waited nothing"
    assert first.scheduled_days == 0, "and nothing was scheduled for it beforehand"

    later = start + dt.timedelta(days=9)
    second = srs.review(first.state, srs.Rating.Good, later, scheduler=scheduler)
    assert second.elapsed_days == 9, "elapsed is the gap between the last review and this one"
    assert second.scheduled_days == max(
        0, (first.state.due - first.state.last_review).days
    ), "scheduled is the interval the scheduler had asked for, not the one the learner took"


def test_reviewing_early_and_late_are_told_apart() -> None:
    """The optimizer needs both numbers because they diverge whenever a learner is off schedule."""
    scheduler = srs.make_scheduler(enable_fuzzing=False)
    start = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
    state = srs.review(srs.new_state(start), srs.Rating.Good, start, scheduler=scheduler).state
    for _ in range(3):  # push it into review with a multi-day interval
        state = srs.review(state, srs.Rating.Good, state.due, scheduler=scheduler).state

    asked = max(0, (state.due - state.last_review).days)
    early = srs.review(state, srs.Rating.Good, state.due - dt.timedelta(days=3), scheduler=scheduler)
    late = srs.review(state, srs.Rating.Good, state.due + dt.timedelta(days=5), scheduler=scheduler)

    assert early.scheduled_days == asked == late.scheduled_days, "the ask does not change"
    assert early.elapsed_days < asked < late.elapsed_days, "but the wait does"
