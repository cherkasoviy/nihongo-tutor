"""FSRS scheduling wrapper.

The rest of the app never imports ``fsrs`` directly: it speaks :class:`SrsState`, a frozen snapshot
of exactly the columns ``cards`` stores. That keeps the library's representation (which changed
between FSRS 5 and 6) out of the schema and lets the scheduler be swapped or re-fitted per user.

Two details of FSRS 6 are worth knowing when reading this module:

* It has no ``New`` state — a never-reviewed card is ``Learning`` with ``step == 0`` and no
  stability yet. Our :class:`~app.db.models.learning.CardState` keeps a distinct ``new`` so a plan
  can tell "introduced today" from "already in the learning steps"; the distinction lives only on
  our side and collapses on the way into the library.
* ``step`` is real state. Dropping it would restart a card's learning steps on every process
  restart, so ``cards.step`` persists it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from fsrs import Card as FsrsCard
from fsrs import Rating, Scheduler, State

from app.db.models.learning import CardState

__all__ = [
    "DEFAULT_DESIRED_RETENTION",
    "Rating",
    "ReviewResult",
    "SrsState",
    "make_scheduler",
    "new_state",
    "retrievability",
    "review",
]

DEFAULT_DESIRED_RETENTION: Final = 0.90
MIN_DESIRED_RETENTION: Final = 0.85
MAX_DESIRED_RETENTION: Final = 0.92

_TO_FSRS: Final[dict[CardState, State]] = {
    CardState.new: State.Learning,
    CardState.learning: State.Learning,
    CardState.review: State.Review,
    CardState.relearning: State.Relearning,
}
_FROM_FSRS: Final[dict[State, CardState]] = {
    State.Learning: CardState.learning,
    State.Review: CardState.review,
    State.Relearning: CardState.relearning,
}


@dataclass(frozen=True, slots=True)
class SrsState:
    """The scheduling half of a card row, detached from the database."""

    state: CardState = CardState.new
    step: int | None = 0
    stability: float | None = None
    difficulty: float | None = None
    due: dt.datetime = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    last_review: dt.datetime | None = None
    reps: int = 0
    lapses: int = 0
    elapsed_days: int = 0
    scheduled_days: int = 0

    @property
    def is_new(self) -> bool:
        return self.state == CardState.new

    def interval(self) -> dt.timedelta:
        """How long the scheduler asked us to wait after the last review."""
        if self.last_review is None:
            return dt.timedelta(0)
        return self.due - self.last_review


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """The new state plus the fields ``review_logs`` needs to reconstruct this review later."""

    state: SrsState
    state_before: CardState
    rating: Rating
    review_at: dt.datetime
    elapsed_days: int
    scheduled_days: int


def make_scheduler(
    *,
    desired_retention: float = DEFAULT_DESIRED_RETENTION,
    parameters: Sequence[float] | None = None,
    enable_fuzzing: bool = True,
) -> Scheduler:
    """Build a scheduler.

    ``parameters`` comes from ``users.fsrs_params`` once the monthly optimizer has enough review
    logs (Phase 4); until then the library defaults apply. Fuzzing spreads same-day due dates so a
    cohort of cards introduced together does not come back as one lump — tests turn it off.
    """
    retention = min(MAX_DESIRED_RETENTION, max(MIN_DESIRED_RETENTION, desired_retention))
    if parameters is None:
        return Scheduler(desired_retention=retention, enable_fuzzing=enable_fuzzing)
    return Scheduler(parameters=tuple(parameters), desired_retention=retention, enable_fuzzing=enable_fuzzing)


def new_state(now: dt.datetime) -> SrsState:
    """A card that exists but has never been shown; due immediately."""
    return SrsState(state=CardState.new, step=0, due=now)


def _to_fsrs(state: SrsState) -> FsrsCard:
    if state.is_new:
        # A virgin card: no stability/difficulty yet, sitting on the first learning step.
        return FsrsCard(state=State.Learning, step=0, due=state.due, last_review=None)
    return FsrsCard(
        state=_TO_FSRS[state.state],
        step=state.step,
        stability=state.stability,
        difficulty=state.difficulty,
        due=state.due,
        last_review=state.last_review,
    )


def _elapsed_days(state: SrsState, now: dt.datetime) -> int:
    if state.last_review is None:
        return 0
    return max(0, (now - state.last_review).days)


def _scheduled_days(state: SrsState) -> int:
    return max(0, state.interval().days)


def review(
    state: SrsState,
    rating: Rating,
    now: dt.datetime,
    *,
    scheduler: Scheduler,
) -> ReviewResult:
    """Grade a card and return its next scheduling state.

    ``Again`` on a card that had reached ``review`` counts as a lapse; intra-session retests pass
    the same ratings through but the caller marks their log rows ``intra_session`` so the optimizer
    ignores them (massed repetition would bias the fit).
    """
    before = state.state
    elapsed = _elapsed_days(state, now)
    scheduled = _scheduled_days(state)

    updated, _log = scheduler.review_card(_to_fsrs(state), rating, now)

    next_state = SrsState(
        state=_FROM_FSRS[updated.state],
        step=updated.step,
        stability=updated.stability,
        difficulty=updated.difficulty,
        due=updated.due,
        last_review=updated.last_review or now,
        reps=state.reps + 1,
        lapses=state.lapses + (1 if rating == Rating.Again and before == CardState.review else 0),
        elapsed_days=elapsed,
        scheduled_days=max(0, (updated.due - now).days),
    )
    return ReviewResult(
        state=next_state,
        state_before=before,
        rating=rating,
        review_at=now,
        elapsed_days=elapsed,
        scheduled_days=scheduled,
    )


def retrievability(state: SrsState, now: dt.datetime, *, scheduler: Scheduler) -> float:
    """Probability of recalling this card right now, in [0, 1].

    A card that has never been reviewed has nothing to forget yet, so it reports 0.0 and sorts to
    the front of a retrievability-ordered warm-up.
    """
    if state.is_new or state.stability is None:
        return 0.0
    value = scheduler.get_card_retrievability(_to_fsrs(state), now)
    return float(value)


def with_due(state: SrsState, due: dt.datetime) -> SrsState:
    """Move a card's due date without touching its memory model (used by the planner to stage
    the production/listening siblings of a freshly introduced item)."""
    return replace(state, due=due)
