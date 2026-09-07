"""Card lifecycle: introduce items, find what is due, persist a graded review.

This is the only place that translates between :class:`~app.domain.srs.SrsState` and the ``cards``
row, so the scheduler's representation never leaks into handlers or routers.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.content import Item, ItemStage
from app.db.models.learning import Card, CardDirection, CardState, ReviewLog
from app.domain import srs
from app.domain.srs import ReviewResult, SrsState

# Kana is drilled both ways: recognise the glyph, and produce it from the Russian reading. The
# listening direction waits for Phase 1's audio, which is deferred with TTS.
KANA_DIRECTIONS: Sequence[CardDirection] = (CardDirection.recognition, CardDirection.production)


def state_of(card: Card) -> SrsState:
    return SrsState(
        state=card.state,
        step=card.step,
        stability=card.stability,
        difficulty=card.difficulty,
        due=card.due,
        last_review=card.last_review,
        reps=card.reps,
        lapses=card.lapses,
        elapsed_days=card.elapsed_days,
        scheduled_days=card.scheduled_days,
    )


def _write_state(card: Card, state: SrsState) -> None:
    card.state = state.state
    card.step = state.step
    card.stability = state.stability
    card.difficulty = state.difficulty
    card.due = state.due
    card.last_review = state.last_review
    card.reps = state.reps
    card.lapses = state.lapses
    card.elapsed_days = state.elapsed_days
    card.scheduled_days = state.scheduled_days


async def introduce_item(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    item_id: uuid.UUID,
    now: dt.datetime,
    directions: Sequence[CardDirection] = KANA_DIRECTIONS,
) -> list[Card]:
    """Ensure the card set for one item exists, and return all of it.

    The plan staggers the siblings: recognition is drilled today, production comes due tomorrow, so a
    learner meets the same syllable in a different guise on the following day rather than twice in a row.

    Returns every card for the requested directions, not only the ones this call created. A syllable
    can be introduced a second time — an abandoned lesson leaves its cards untouched and the item
    goes back in the queue — and a caller that only saw *new* cards would build that second lesson
    with an introduction and no checks behind it.
    """
    existing = {
        card.direction: card
        for card in await session.scalars(select(Card).where(Card.user_id == user_id, Card.item_id == item_id))
    }
    cards: list[Card] = []
    created = False
    for offset, direction in enumerate(directions):
        card = existing.get(direction)
        if card is None:
            card = Card(
                user_id=user_id,
                item_id=item_id,
                direction=direction,
                state=CardState.new,
                step=0,
                due=now + dt.timedelta(days=offset),
            )
            session.add(card)
            created = True
        cards.append(card)
    if created:
        await session.flush()
    return cards


def _due_query(user_id: uuid.UUID, now: dt.datetime) -> Select[tuple[Card]]:
    return (
        select(Card)
        .where(
            Card.user_id == user_id,
            Card.suspended.is_(False),
            Card.due <= now,
            Card.state != CardState.new,
        )
        .order_by(Card.due)
    )


async def due_cards(session: AsyncSession, *, user_id: uuid.UUID, now: dt.datetime, limit: int) -> list[Card]:
    if limit <= 0:
        return []
    return list(await session.scalars(_due_query(user_id, now).limit(limit)))


async def due_count(session: AsyncSession, *, user_id: uuid.UUID, now: dt.datetime) -> int:
    stmt = select(func.count()).select_from(_due_query(user_id, now).subquery())
    return int((await session.execute(stmt)).scalar_one())


def _taught_items(user_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    """Items this learner has genuinely started on.

    Having a card is not the same as having been taught. ``introduce_item`` creates cards the moment
    the planner *decides* to introduce something, and a sitting that is then abandoned — stopped, or
    left open across local midnight — leaves those cards sitting in ``new`` with nothing ever asked
    of them. Keying "introduced" off the mere existence of a row put such syllables in a hole: never
    offered again, never due (the due queries skip ``new``), and counted as learned.

    So an item counts as taught only once one of its cards has either left ``new`` or collected a
    review log. Everything else is still owed to the learner. ``introduce_item`` skips directions
    that already exist, so re-offering an item reuses its untouched cards instead of duplicating them.
    """
    return select(Card.item_id).where(
        Card.user_id == user_id,
        or_(
            Card.state != CardState.new,
            select(ReviewLog.id).where(ReviewLog.card_id == Card.id).exists(),
        ),
    )


async def next_new_items(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    stages: Sequence[ItemStage],
    limit: int,
) -> list[Item]:
    """The next items the learner has not been taught yet, in curriculum order."""
    if limit <= 0:
        return []
    already = _taught_items(user_id)
    stmt = (
        select(Item)
        .where(Item.active.is_(True), Item.stage.in_(stages), Item.id.not_in(already))
        .order_by(Item.curriculum_order)
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def remaining_new_count(session: AsyncSession, *, user_id: uuid.UUID, stages: Sequence[ItemStage]) -> int:
    """How much of the curriculum is still owed. Mirrors :func:`next_new_items` exactly."""
    already = _taught_items(user_id)
    stmt = select(func.count()).select_from(
        select(Item.id).where(Item.active.is_(True), Item.stage.in_(stages), Item.id.not_in(already)).subquery()
    )
    return int((await session.execute(stmt)).scalar_one())


async def weakest_cards(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    limit: int,
    exclude: Sequence[uuid.UUID] = (),
) -> list[Card]:
    """Introduced cards the learner is closest to forgetting, soonest-due first.

    Used to pad an extra practice session when nothing is genuinely due. These are *not* reviews —
    the caller logs them with ``intra_session=True`` and does not reschedule, because grading a card
    the scheduler did not ask for would push its real review out on the strength of a cram.
    """
    if limit <= 0:
        return []
    stmt = (
        select(Card)
        .where(Card.user_id == user_id, Card.suspended.is_(False), Card.state != CardState.new)
        .order_by(Card.due)
        .limit(limit)
    )
    if exclude:
        stmt = stmt.where(Card.id.not_in(exclude))
    return list(await session.scalars(stmt))


async def record_review(
    session: AsyncSession,
    *,
    card: Card,
    result: ReviewResult,
    response_ms: int | None,
    auto_graded: bool,
    intra_session: bool,
    answer_payload: dict[str, object] | None = None,
    apply_state: bool = True,
) -> ReviewLog:
    """Persist a grade: optionally update the card, always append the immutable log row.

    ``intra_session`` marks the massed retests a session does within itself. Those are logged for the
    record but must not move the schedule (``apply_state=False``): repeating an item four times in
    fifteen minutes says nothing about how long it will survive, and letting each retest reschedule
    the card would push its next real review out by weeks on the strength of an echo. The plan
    excludes the same rows from the FSRS optimizer for that reason.
    """
    if apply_state:
        _write_state(card, result.state)
    log_row = ReviewLog(
        card_id=card.id,
        rating=int(result.rating),
        review_at=result.review_at,
        elapsed_days=result.elapsed_days,
        scheduled_days=result.scheduled_days,
        state_before=result.state_before,
        response_ms=response_ms,
        auto_graded=auto_graded,
        intra_session=intra_session,
        answer_payload=dict(answer_payload) if answer_payload else None,
    )
    session.add(log_row)
    await session.flush()
    return log_row


async def retention_7d(
    session: AsyncSession, *, user_id: uuid.UUID, now: dt.datetime, min_reviews: int = 10
) -> float | None:
    """Share of genuine (non-massed) reviews in the last week that were recalled.

    Returns ``None`` until there is enough history to mean anything — the planner treats that as
    "make no adjustment" rather than assuming the worst.
    """
    since = now - dt.timedelta(days=7)
    stmt = (
        select(
            func.count().label("total"),
            func.count().filter(ReviewLog.rating >= int(srs.Rating.Good)).label("recalled"),
        )
        .select_from(ReviewLog)
        .join(Card, Card.id == ReviewLog.card_id)
        .where(
            Card.user_id == user_id,
            ReviewLog.review_at >= since,
            ReviewLog.intra_session.is_(False),
        )
    )
    row = (await session.execute(stmt)).one()
    total = int(row.total)
    if total < min_reviews:
        return None
    return float(row.recalled) / total
