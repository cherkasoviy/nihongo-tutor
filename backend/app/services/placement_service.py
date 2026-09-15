"""Placement: letting a learner say which syllables they already know.

Two people can share this curriculum from opposite ends. A complete beginner needs all 208 taught;
someone who already reads the gojūon needs the app to skip past a month of material they know and
get to what they don't. Without this, the second learner's first three weeks are a formality.

The design decision worth stating: **"I know this" seeds a card, it does not skip one.** Self-report
on kana is unreliable in a specific way — people are confident on あ-row and mushy on ぢ/づ, りゃ/りゅ/りょ,
シ versus ツ. So a claimed syllable is written as though the learner had answered it correctly twice,
which is the real scheduler's own state for exactly that history, and its due date is pulled forward
into a spread window. Every claim is therefore *tested* within a couple of weeks: the ones that hold
shoot out to long intervals on a Good, and the ones that do not drop into relearning. The scheduler
verifies the claim instead of trusting it, which is a placement test without building one.

Two consequences that fall out for free:

* A ``review``-state card comes back as free recall rather than a four-option grid, so a learner's
  first encounter with a syllable they claim to know is "what does this say?" with nothing on
  screen — which is the right test — while a beginner on the same engine still gets the grid.
* The Phase 4 FSRS optimizer reads ``review_logs``, and a seeded card has none, so a synthetic
  prior can never contaminate a fitted parameter set.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.content import Item, ItemType
from app.db.models.learning import Card, CardDirection, CardState, ReviewLog
from app.domain import srs
from app.logging import get_logger
from app.services.card_service import KANA_DIRECTIONS

log = get_logger(__name__)

# Claimed cards are spread across a window rather than all landing on one day: a learner marking a
# hundred syllables known would otherwise wake up to two hundred reviews, the backlog gate would
# throttle their new items to zero, and placement would have made the app slower.
SEEDED_REVIEWS_PER_DAY: Final = 12


@dataclass(frozen=True, slots=True)
class PlacementResult:
    seeded: int = 0
    skipped_already_reviewed: int = 0
    cleared: int = 0


def _known_prior(now: dt.datetime) -> srs.SrsState:
    """The state the real scheduler produces for a card answered correctly twice.

    Deliberately taken from the library rather than invented. Two Goods is the shortest honest path
    into ``review``, and it lands on a stability of a couple of days — long enough not to drill the
    syllable immediately, short enough that an over-confident claim is caught within the week.
    """
    scheduler = srs.make_scheduler(enable_fuzzing=False)
    state = srs.new_state(now)
    for _ in range(2):
        state = srs.review(state, srs.Rating.Good, now, scheduler=scheduler).state
    return state


async def _kana_items_in_order(session: AsyncSession, item_ids: Sequence[uuid.UUID]) -> list[Item]:
    if not item_ids:
        return []
    stmt = select(Item).where(Item.id.in_(item_ids), Item.type == ItemType.kana).order_by(Item.curriculum_order)
    return list(await session.scalars(stmt))


async def mark_known(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    item_ids: Sequence[uuid.UUID],
    now: dt.datetime,
    per_day: int = SEEDED_REVIEWS_PER_DAY,
) -> PlacementResult:
    """Record that the learner already knows these syllables, and queue them for verification.

    Idempotent, and it never overwrites earned history: a card that already carries a review log is
    left exactly as the learner's own answers left it. Claims are ordered by curriculum position, so
    the syllables a learner met first are also the first ones checked.
    """
    items = await _kana_items_in_order(session, item_ids)
    if not items:
        return PlacementResult()

    prior = _known_prior(now)
    existing = {
        (card.item_id, card.direction): card
        for card in await session.scalars(
            select(Card).where(Card.user_id == user_id, Card.item_id.in_([i.id for i in items]))
        )
    }
    reviewed = set(
        await session.scalars(
            select(ReviewLog.card_id).where(ReviewLog.card_id.in_([c.id for c in existing.values()] or [None]))
        )
    )

    seeded = skipped = 0
    for item in items:
        for direction in KANA_DIRECTIONS:
            card = existing.get((item.id, direction))
            if card is not None and card.id in reviewed:
                skipped += 1
                continue
            if card is None:
                card = Card(user_id=user_id, item_id=item.id, direction=direction)
                session.add(card)

            # One day per `per_day` claims, so the verification queue is level rather than a spike.
            offset = 1 + (seeded // max(1, per_day))
            card.state = prior.state
            card.step = prior.step
            card.stability = prior.stability
            card.difficulty = prior.difficulty
            card.last_review = now
            card.due = now + dt.timedelta(days=offset)
            card.reps = prior.reps
            card.lapses = 0
            card.elapsed_days = 0
            card.scheduled_days = offset
            card.suspended = False
            seeded += 1

    await session.flush()
    log.info("placement recorded", user_id=str(user_id), seeded=seeded, skipped=skipped)
    return PlacementResult(seeded=seeded, skipped_already_reviewed=skipped)


async def unmark_known(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    item_ids: Sequence[uuid.UUID],
    now: dt.datetime,
) -> PlacementResult:
    """Undo a claim, as long as it was never actually tested.

    A card the learner has answered is real history and stays: "I do not know this after all" is
    what answering Again is for.

    Resets the untested cards rather than deleting them. The goal is only to put the syllable back
    in the teaching queue, and ``card_service._taught_items`` already treats a card in ``new`` with
    no review log as untaught — so a reset achieves exactly what a delete did. Deleting achieved
    something else as well: ``session_steps.card_id`` used to cascade, so un-claiming a syllable in
    the middle of a lesson silently destroyed that lesson's pending steps, and the sitting then
    closed itself as "completed" with most of its work gone. The cascade is fixed too, but the
    delete was never needed in the first place.
    """
    items = await _kana_items_in_order(session, item_ids)
    if not items:
        return PlacementResult()

    untested = list(
        await session.scalars(
            select(Card.id).where(
                Card.user_id == user_id,
                Card.item_id.in_([i.id for i in items]),
                ~select(ReviewLog.id).where(ReviewLog.card_id == Card.id).exists(),
            )
        )
    )
    if not untested:
        return PlacementResult()
    await session.execute(
        update(Card).where(Card.id.in_(untested))
        # Exactly the shape ``card_service.introduce_item`` gives a brand-new card. ``due`` is
        # NOT NULL and is meaningless while a card is ``new`` — the due queries skip that state —
        # so ``now`` is simply the honest value to leave in it.
        .values(
            state=CardState.new,
            step=0,
            stability=None,
            difficulty=None,
            due=now,
            last_review=None,
            reps=0,
            lapses=0,
            elapsed_days=0,
            scheduled_days=0,
            suspended=False,
        )
    )
    await session.flush()
    cleared = len(untested)
    log.info("placement cleared", user_id=str(user_id), cleared=cleared)
    return PlacementResult(cleared=cleared)


async def claimed_but_unverified(session: AsyncSession, *, user_id: uuid.UUID) -> int:
    """Syllables the learner asserted and the scheduler has not checked yet.

    Counted in *syllables*, on the recognition card, so it sits directly beside the "known" figure
    rather than reading twice as large because each syllable carries two cards.

    Kept separate from "known" in the stats: showing 90% mastery on day one because someone ticked
    a box would be the app flattering them with a number it has not earned.
    """
    stmt = select(func.count(func.distinct(Card.item_id))).where(
        Card.user_id == user_id,
        Card.direction == CardDirection.recognition,
        Card.state != CardState.new,
        ~select(ReviewLog.id).where(ReviewLog.card_id == Card.id).exists(),
    )
    return int((await session.execute(stmt)).scalar_one())


@dataclass(frozen=True, slots=True)
class PlacementPreview:
    """What a claim is about to do, in the learner's terms."""

    syllables: int
    cards: int
    already_tested: int
    per_day: int
    days: int


async def preview(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    item_ids: Sequence[uuid.UUID],
    per_day: int = SEEDED_REVIEWS_PER_DAY,
) -> PlacementPreview:
    """The shape of a claim before it is applied.

    Computed here rather than in the Mini App so the number the learner agrees to is produced by
    the same code that does the seeding — including the part where a syllable she has already been
    tested on is left alone. A claim is a big, quiet decision; she should be able to see it first.
    """
    items = await _kana_items_in_order(session, item_ids)
    if not items:
        return PlacementPreview(syllables=0, cards=0, already_tested=0, per_day=per_day, days=0)

    existing = {
        (card.item_id, card.direction): card
        for card in await session.scalars(
            select(Card).where(Card.user_id == user_id, Card.item_id.in_([i.id for i in items]))
        )
    }
    reviewed = set(
        await session.scalars(
            select(ReviewLog.card_id).where(ReviewLog.card_id.in_([c.id for c in existing.values()] or [None]))
        )
    )

    seeded = skipped = 0
    touched: set[uuid.UUID] = set()
    for item in items:
        for direction in KANA_DIRECTIONS:
            card = existing.get((item.id, direction))
            if card is not None and card.id in reviewed:
                skipped += 1
                continue
            seeded += 1
            touched.add(item.id)

    days = -(-seeded // max(1, per_day)) if seeded else 0
    return PlacementPreview(
        syllables=len(touched),
        cards=seeded,
        already_tested=skipped,
        per_day=per_day,
        days=days,
    )
