"""A simulated learner walking 30 consecutive days through the kana bootcamp.

This is the plan's headline Phase 1 check, and running it surfaced a genuine inconsistency in the
plan's own numbers, so the assertions below deliberately differ from its one-line description
("a simulated 30-day learner completing kana within 12-20 min/day"):

* **Session length.** The plan fixes ``base = 5 kana/day`` in the kana stage, and its only upward
  nudge is ``new = min(new + 1, 10)`` — an increment on the base, so six is the real ceiling for a
  kana day, not ten. Six new syllables cost four steps each (intro, immediate check, delayed
  production drill, wrap-up retest); with the day's due reviews on top a session lands at roughly
  25-45 steps, which is 3-6 minutes at the planner's own 8 s/step, not 12-20. Reaching twenty
  minutes would need something like 20-35 new syllables a day, which contradicts ``base = 5``.
* **Finishing the syllabary.** 208 syllables at a ceiling of six a day is ~35 days at best, so no
  learner completes *all* kana inside 30. Hiragana alone (104) finishes comfortably, and that is
  what the bootcamp gates on.

Rather than assert numbers the algorithm cannot produce, this test pins the properties that are
both true and worth defending: the day always fits inside its budget, the streak survives 30
consecutive days, hiragana is finished well inside the month, and the adaptive rules visibly
throttle a struggling learner relative to a strong one. Raising the kana-stage base is a product
decision for the plan's author, not something to smuggle in through a test.
"""

from __future__ import annotations

import datetime as dt
import random
import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content_pipeline.import_kana import import_kana
from app.db.models.content import Item, ItemStage, ItemType
from app.db.models.learning import Card, DailyPlan, LearningSession, SessionOutcome, SessionStep, StepKind, Streak
from app.db.models.users import User, UserRole
from app.domain.session_planner import BACKLOG_PAUSE_RATIO, DEFAULT_AVG_REVIEW_SECONDS
from app.services import session_service, user_service
from app.services.user_service import TelegramIdentity
from tests.conftest import fresh_tg_id

pytestmark = pytest.mark.integration

DAYS = 30
TIMEZONE = "Europe/Berlin"
HIRAGANA_TOTAL = 104
KANA_TOTAL = 208
# The plan's daily target is 17 minutes; a session must never blow through it.
MAX_SESSION_MINUTES = 20.0


@dataclass
class Simulation:
    """What 30 days of one learner looked like."""

    minutes: list[float] = field(default_factory=list)
    new_per_day: list[int] = field(default_factory=list)
    backlog_ratios: list[float] = field(default_factory=list)
    completed_days: int = 0
    introduced: int = 0
    hiragana_introduced: int = 0
    streak: int = 0


async def _simulate(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    accuracy: float,
    seed: int = 7,
    days: int = DAYS,
) -> Simulation:
    """Run ``days`` consecutive days, answering each step correctly with probability ``accuracy``."""
    rng = random.Random(seed)
    start = dt.datetime(2026, 4, 1, 9, 0, tzinfo=dt.UTC)
    tg_id = fresh_tg_id()

    async with sessionmaker() as s:
        await import_kana(s)
        user = await user_service.create_user(
            s,
            TelegramIdentity(tg_user_id=tg_id, first_name="Аня"),
            role=UserRole.learner,
            invited_by=None,
            daily_budget_usd=0.35,
            now=start,
        )
        user.timezone = TIMEZONE
        await s.commit()
        user_id = user.id

    sim = Simulation()
    for day in range(days):
        now = start + dt.timedelta(days=day)
        async with sessionmaker() as s:
            learner = await s.get(User, user_id)
            assert learner is not None
            learning, _ = await session_service.start_or_resume(s, user=learner, now=now)
            await s.commit()

            sim.minutes.append(learning.planned_steps * DEFAULT_AVG_REVIEW_SECONDS / 60)
            sim.new_per_day.append(
                int(
                    (
                        await s.execute(
                            select(func.count())
                            .select_from(SessionStep)
                            .where(
                                SessionStep.session_id == learning.id,
                                SessionStep.kind == StepKind.intro_item,
                            )
                        )
                    ).scalar_one()
                )
            )
            plan = await s.get(DailyPlan, (user_id, learning.local_date))
            if plan is not None:
                sim.backlog_ratios.append(plan.backlog_ratio)

            for _ in range(600):
                step = await session_service.next_step(s, learning_session_id=learning.id)
                if step is None:
                    break
                await session_service.mark_shown(s, step=step, now=now, message_id=None)
                if step.payload.get("mode") == "ack":
                    await session_service.acknowledge(s, step=step, now=now)
                else:
                    correct = int(step.payload["correct"])
                    options = max(1, len(step.payload["choices"]))
                    pick = correct if rng.random() < accuracy else (correct + 1) % options
                    await session_service.submit_choice(s, user=learner, step=step, choice=pick, now=now)

            await session_service.finish(s, user=learner, learning=learning, now=now)
            await s.commit()

    async with sessionmaker() as s:
        sim.completed_days = int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(LearningSession)
                    .where(
                        LearningSession.user_id == user_id,
                        LearningSession.outcome == SessionOutcome.completed,
                    )
                )
            ).scalar_one()
        )
        sim.introduced = int(
            (
                await s.execute(select(func.count(func.distinct(Card.item_id))).where(Card.user_id == user_id))
            ).scalar_one()
        )
        sim.hiragana_introduced = int(
            (
                await s.execute(
                    select(func.count(func.distinct(Card.item_id)))
                    .join(Item, Item.id == Card.item_id)
                    .where(Card.user_id == user_id, Item.stage == ItemStage.kana_hira)
                )
            ).scalar_one()
        )
        streak = await s.get(Streak, user_id)
        sim.streak = streak.current if streak else 0
    return sim


async def test_a_diligent_learner_finishes_hiragana_and_keeps_a_30_day_streak(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    sim = await _simulate(sessionmaker, accuracy=1.0)

    assert sim.completed_days == DAYS
    assert sim.streak == DAYS, "30 consecutive days must not break the streak or spend a freeze"
    assert sim.hiragana_introduced == HIRAGANA_TOTAL, "the bootcamp gates on hiragana; it must finish"
    assert sim.introduced > HIRAGANA_TOTAL, "and the learner should be well into katakana by day 30"
    assert (
        sim.introduced < KANA_TOTAL
    ), "documented ceiling: at 6 new/day the plan's own algorithm cannot cover 208 syllables in 30 days"


async def test_no_session_ever_exceeds_the_daily_budget(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The safety property that actually matters: the day never runs away from the learner."""
    sim = await _simulate(sessionmaker, accuracy=0.85)

    assert max(sim.minutes) <= MAX_SESSION_MINUTES, f"longest session was {max(sim.minutes):.1f} min"
    assert max(sim.backlog_ratios) <= BACKLOG_PAUSE_RATIO, "a daily learner must never build a backlog"
    assert sim.completed_days == DAYS


async def test_the_planner_throttles_a_struggling_learner(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The adaptive rule is the point of the planner: weaker recall must slow the intake down."""
    strong = await _simulate(sessionmaker, accuracy=1.0, days=20)
    struggling = await _simulate(sessionmaker, accuracy=0.6, days=20)

    assert struggling.introduced < strong.introduced
    assert min(struggling.new_per_day) < min(strong.new_per_day)
    # Slowed down, never stopped: the plan's floor keeps at least one new item on a bad day.
    assert min(struggling.new_per_day) >= 1
    assert struggling.completed_days == 20, "a struggling learner still finishes the day and keeps the habit"


async def test_the_curriculum_is_introduced_in_order_without_gaps(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """New items follow curriculum_order, so hiragana is exhausted before katakana starts."""
    await _simulate(sessionmaker, accuracy=1.0, days=10)

    async with sessionmaker() as s:
        user_id = (await s.scalars(select(User.id))).one()
        rows = list(
            await s.execute(
                select(Item.curriculum_order)
                .join(Card, Card.item_id == Item.id)
                .where(Card.user_id == user_id, Item.type == ItemType.kana)
                .distinct()
                .order_by(Item.curriculum_order)
            )
        )
        orders = [r[0] for r in rows]

    assert orders == list(range(1, len(orders) + 1)), "introduced items must be a prefix of the curriculum"


async def test_no_grid_ever_offers_the_same_reading_twice(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """No grid may offer the same visible label twice, whatever the builder assembles.

    Polivanov gives distinct characters identical readings — お and を are both «о», じ and ぢ both
    «дзи» — so a grid that offered both would mark a learner wrong for reading correctly.

    This is a broad net over whatever the real builder happens to produce, not a reproduction: it
    only catches the bug when the RNG actually draws a homophone into a grid. The deterministic
    guarantee lives in tests/unit/test_distractors.py, which pins the property directly.
    """
    await _simulate(sessionmaker, accuracy=1.0, days=15)

    async with sessionmaker() as s:
        grids = [
            row.payload["choices"]
            for row in await s.scalars(select(SessionStep))
            if row.payload.get("mode") == "choice"
        ]

    assert grids, "the simulation produced no multiple-choice steps"
    duplicated = [g for g in grids if len(g) != len(set(g))]
    assert not duplicated, f"{len(duplicated)} grid(s) offered the same label twice, e.g. {duplicated[:3]}"


async def test_reopening_a_day_replays_the_same_session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The plan's seeded RNG promise: closing and reopening today must not reshuffle it."""
    now = dt.datetime(2026, 4, 1, 9, 0, tzinfo=dt.UTC)
    async with sessionmaker() as s:
        await import_kana(s)
        user = await user_service.create_user(
            s,
            TelegramIdentity(tg_user_id=fresh_tg_id(), first_name="Аня"),
            role=UserRole.learner,
            invited_by=None,
            daily_budget_usd=0.35,
            now=now,
        )
        user.timezone = TIMEZONE
        await s.commit()
        user_id: uuid.UUID = user.id

        first, created = await session_service.start_or_resume(s, user=user, now=now)
        await s.commit()
        assert created
        order_before = [
            (row.idx, row.kind, row.payload.get("char"))
            for row in await s.scalars(
                select(SessionStep).where(SessionStep.session_id == first.id).order_by(SessionStep.idx)
            )
        ]

    async with sessionmaker() as s:
        learner = await s.get(User, user_id)
        assert learner is not None
        again, created_again = await session_service.start_or_resume(s, user=learner, now=now + dt.timedelta(hours=3))
        await s.commit()
        assert not created_again and again.id == first.id
        order_after = [
            (row.idx, row.kind, row.payload.get("char"))
            for row in await s.scalars(
                select(SessionStep).where(SessionStep.session_id == again.id).order_by(SessionStep.idx)
            )
        ]

    assert order_before == order_after
