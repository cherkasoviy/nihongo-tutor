"""Numbers behind ``/stats`` and the Mini App progress screen."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.content import Item, ItemStage, ItemType
from app.db.models.learning import (
    Card,
    CardDirection,
    CardState,
    LearningSession,
    ReviewLog,
    SessionOutcome,
    Streak,
)
from app.services import card_service, placement_service


@dataclass(frozen=True, slots=True)
class LearnerStats:
    kana_total: int
    kana_introduced: int
    kana_known: int
    kana_claimed: int
    due_now: int
    reviews_7d: int
    retention_7d: float | None
    streak_current: int
    streak_longest: int
    freezes_available: int
    sessions_completed: int
    minutes_7d: float


# A syllable counts as "known" once its recognition card has left the learning steps and survived a
# real interval. Reps alone would count four massed retests in one session as mastery — and a
# placement claim would count as mastery on the strength of a tick, so "known" also requires that
# the learner has actually answered the card at least once.
KNOWN_STATES = (CardState.review,)


async def learner_stats(session: AsyncSession, *, user_id: uuid.UUID, now: dt.datetime) -> LearnerStats:
    kana_total = int(
        (
            await session.execute(
                select(func.count()).select_from(Item).where(Item.type == ItemType.kana, Item.active.is_(True))
            )
        ).scalar_one()
    )

    introduced_stmt = (
        select(func.count(func.distinct(Card.item_id)))
        .join(Item, Item.id == Card.item_id)
        .where(Card.user_id == user_id, Item.type == ItemType.kana)
    )
    kana_introduced = int((await session.execute(introduced_stmt)).scalar_one())

    verified = select(ReviewLog.id).where(ReviewLog.card_id == Card.id).exists()
    known_stmt = (
        select(func.count(func.distinct(Card.item_id)))
        .join(Item, Item.id == Card.item_id)
        .where(
            Card.user_id == user_id,
            Item.type == ItemType.kana,
            Card.direction == CardDirection.recognition,
            Card.state.in_(KNOWN_STATES),
            verified,
        )
    )
    kana_known = int((await session.execute(known_stmt)).scalar_one())

    since = now - dt.timedelta(days=7)
    reviews_7d = int(
        (
            await session.execute(
                select(func.count())
                .select_from(ReviewLog)
                .join(Card, Card.id == ReviewLog.card_id)
                .where(
                    Card.user_id == user_id,
                    ReviewLog.review_at >= since,
                    ReviewLog.intra_session.is_(False),
                )
            )
        ).scalar_one()
    )

    sessions_completed = int(
        (
            await session.execute(
                select(func.count())
                .select_from(LearningSession)
                .where(
                    LearningSession.user_id == user_id,
                    LearningSession.outcome == SessionOutcome.completed,
                )
            )
        ).scalar_one()
    )
    active_ms_7d = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(LearningSession.active_ms), 0)).where(
                    LearningSession.user_id == user_id,
                    LearningSession.started_at >= since,
                )
            )
        ).scalar_one()
    )

    streak = await session.get(Streak, user_id)

    return LearnerStats(
        kana_total=kana_total,
        kana_introduced=kana_introduced,
        kana_known=kana_known,
        kana_claimed=await placement_service.claimed_but_unverified(session, user_id=user_id),
        due_now=await card_service.due_count(session, user_id=user_id, now=now),
        reviews_7d=reviews_7d,
        retention_7d=await card_service.retention_7d(session, user_id=user_id, now=now),
        streak_current=streak.current if streak else 0,
        streak_longest=streak.longest if streak else 0,
        freezes_available=streak.freezes_available if streak else 0,
        sessions_completed=sessions_completed,
        minutes_7d=round(active_ms_7d / 60_000, 1),
    )


async def stage_for(session: AsyncSession, *, user_id: uuid.UUID) -> ItemStage:
    """Which stage the learner is in: hiragana until it is exhausted, then katakana, then core.

    Progression is by "nothing left to introduce", not by mastery, because reviews of earlier
    syllables keep flowing through the session regardless of which stage new items come from.
    """
    for stage in (ItemStage.kana_hira, ItemStage.kana_kata):
        remaining = await card_service.remaining_new_count(session, user_id=user_id, stages=[stage])
        if remaining > 0:
            return stage
    return ItemStage.core
