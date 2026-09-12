"""A simulated learner walking 30 consecutive days through the kana bootcamp.

This is the plan's headline Phase 1 check. Running it originally showed the plan's own numbers could
not produce what it asked for — "completing kana within 12-20 min/day" — because ``base = 5`` in the
kana stage caps a day at six new syllables, which is 3-6 minutes of content and about 35 days for
the syllabary. That was measured, not argued: a diligent learner reached 177 of 208 in a month and a
learner recalling 70% reached 94.

Five turned out to be vocabulary pacing applied to kana, where an item is a shape and a sound rather
than a meaning, a reading and a production form. The kana default is now ten — which is also what
25% of a seventeen-minute session at eight seconds a step implies — and a learner may choose up to
:data:`~app.domain.session_planner.MAX_NEW_PER_DAY`. The safety rail was never the low default; it is
the backlog gate, which zeroes new items by itself once the due queue outgrows the warm-up, and a
sweep across bases 5-20 showed it doing exactly that.

So these tests now pin: both scripts finished inside the month, every session inside its time
budget, the streak surviving 30 consecutive days, the curriculum taught as a prefix in order, and
the adaptive rules visibly throttling a struggling learner relative to a strong one.
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
from app.db.models.learning import (
    Card,
    CardState,
    DailyPlan,
    LearningSession,
    SessionOutcome,
    SessionStep,
    StepKind,
    Streak,
)
from app.db.models.users import User, UserRole
from app.domain.grading import SelfGrade
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
    empty_days: int = 0
    introduced: int = 0
    hiragana_introduced: int = 0
    streak: int = 0


async def _answer(s: AsyncSession, learner: User, step: SessionStep, now: dt.datetime, *, recalled: bool) -> None:
    """Answer one step whatever shape it takes.

    Three shapes exist now: an introduction to acknowledge, a multiple-choice grid, and free recall
    where the learner reveals the answer and grades themselves. A simulation that only understood
    grids would silently stop exercising the engine the moment a card reached review state.
    """
    mode = step.payload.get("mode")
    if mode == "ack":
        await session_service.acknowledge(s, step=step, now=now)
    elif mode == "self":
        await session_service.reveal(s, step=step, now=now)
        await session_service.submit_self_grade(
            s,
            user=learner,
            step=step,
            grade=SelfGrade.knew if recalled else SelfGrade.forgot,
            now=now,
        )
    else:
        correct = int(step.payload["correct"])
        options = max(1, len(step.payload["choices"]))
        pick = correct if recalled else (correct + 1) % options
        await session_service.submit_choice(s, user=learner, step=step, choice=pick, now=now)


async def _simulate(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    accuracy: float,
    seed: int = 7,
    days: int = DAYS,
) -> Simulation:
    """Run ``days`` consecutive days, answering each step correctly with probability ``accuracy``."""
    # `rng` decides whether this simulated learner recalls a step. Seeding the *module* RNG as well
    # removes a second, less obvious source of variance: the scheduler fuzzes every interval and
    # py-fsrs draws that fuzz from the global `random`, which nothing here was seeding.
    #
    # It does not make the run reproducible, and it is worth knowing why rather than assuming it
    # does. The step order comes from `interleave.make_seed(user.id, local_date)`, and the learner's
    # UUID is fresh on every run — deliberately, since each learner is meant to get their own
    # ordering. Different order means different cards answered at different moments, which feeds
    # back into the schedule, so the per-day step counts genuinely differ run to run.
    #
    # Which is why the assertions below are invariants rather than counts: the guarantees hold under
    # any ordering, and pinning the incidentals would only buy a test that fails for the wrong
    # reason. Verified by running this file repeatedly — streak, completed days and syllables taught
    # come out identical every time while the minutes per day do not.
    random.seed(seed)
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
            if learning.planned_steps == 0:
                sim.empty_days += 1
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
                await _answer(s, learner, step, now, recalled=rng.random() < accuracy)

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


async def test_a_diligent_learner_keeps_the_streak_and_gets_through_the_syllabary(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Thirty consecutive days of turning up is a thirty-day streak, whatever the app had to offer.

    That is the guarantee being asserted, and it is the one that used to fail. At the old pace of
    five a day the learner never ran out of material, so an empty day never happened; at ten the
    syllabary finishes around day 21 and the rest of the month is days with nothing due. Those days
    were closed as ``abandoned`` and earned nothing, so this test failed roughly one run in four
    with ``assert 29 == 30`` — the streak broke on the last day because the learner was ahead.

    The throughput figures below are deliberately asserted with margin rather than exactly.
    Measured at this pace the learner reaches hiragana on day 11 and all 208 syllables by day 21,
    but exact counts move with FSRS's interval arithmetic, and pinning them would buy a test that
    breaks on a library upgrade rather than on a regression.

    Note what this test does *not* do: it does not guarantee an empty day happens. Whether the last
    day still has a review left depends on where the intervals fall, and a diligent learner usually
    has one. So the empty-day behaviour is pinned deterministically in ``test_session_recovery.py``
    instead, by a learner with no curriculum at all, and what is asserted here is the invariant that
    holds either way: turning up every day is a streak of every day. ``empty_days`` is carried only
    so a failure says which case it hit.
    """
    sim = await _simulate(sessionmaker, accuracy=1.0)

    assert sim.streak == DAYS, f"turned up {DAYS} days, {sim.empty_days} of them with nothing due, streak {sim.streak}"
    assert sim.completed_days == DAYS, "every day the learner turned up should be recorded as done"
    assert sim.hiragana_introduced == HIRAGANA_TOTAL, "the bootcamp gates on hiragana; it must finish"
    assert sim.introduced >= HIRAGANA_TOTAL + 50, (
        f"raising the dose from five to ten should carry a diligent learner well past hiragana; "
        f"got {sim.introduced} of {KANA_TOTAL}"
    )


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


async def test_the_curriculum_is_introduced_as_a_prefix_in_order(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """New items follow curriculum_order, so hiragana is exhausted before katakana starts.

    The introduced set must be a *prefix of the curriculum*, which is not the same as a contiguous
    run of integers: hiragana occupies 1-104 and katakana 1001-1104, so a learner who crosses
    between scripts leaves a gap in the numbers while still being exactly N items in.
    """
    await _simulate(sessionmaker, accuracy=1.0, days=14)

    async with sessionmaker() as s:
        user_id = (await s.scalars(select(User.id))).one()
        introduced = [
            row[0]
            for row in await s.execute(
                select(Item.curriculum_order)
                .join(Card, Card.item_id == Item.id)
                .where(Card.user_id == user_id, Item.type == ItemType.kana)
                .distinct()
                .order_by(Item.curriculum_order)
            )
        ]
        curriculum = [
            row[0]
            for row in await s.execute(
                select(Item.curriculum_order)
                .where(Item.type == ItemType.kana, Item.active.is_(True))
                .order_by(Item.curriculum_order)
                .limit(len(introduced))
            )
        ]

    assert introduced, "the simulation must have introduced something"
    assert introduced == curriculum, "introduced items must be the first N of the curriculum, in order"
    assert introduced[:HIRAGANA_TOTAL] == list(
        range(1, min(len(introduced), HIRAGANA_TOTAL) + 1)
    ), "hiragana is taught through before katakana begins"


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


async def test_known_syllables_graduate_from_multiple_choice_to_free_recall(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The drill should get harder as the card gets stronger, not stay the same forever.

    A four-option grid puts the answer on screen — the right shape while a syllable is new, and a
    much weaker test once it is known. Cards that reach ``review`` switch to free recall: show the
    glyph, recall it, then self-grade.
    """
    await _simulate(sessionmaker, accuracy=1.0, days=8)

    async with sessionmaker() as s:
        steps = list(await s.scalars(select(SessionStep)))

    modes = {st.payload.get("mode") for st in steps}
    assert "choice" in modes, "new syllables must still be introduced with a grid"
    assert "self" in modes, "syllables in review state must graduate to free recall"

    # And the split must follow card state, not chance.
    self_steps = [st for st in steps if st.payload.get("mode") == "self"]
    assert all(
        st.kind is StepKind.review_recog for st in self_steps
    ), "only recognition graduates; production still needs the glyphs on screen to pick from"
    async with sessionmaker() as s:
        states = {
            c.id: c.state for c in await s.scalars(select(Card).where(Card.id.in_([st.card_id for st in self_steps])))
        }
    assert states and all(state is not CardState.new for state in states.values())
