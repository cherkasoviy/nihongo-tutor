"""The session engine: build a day's lesson, serve it a step at a time, grade the answers.

Both clients share this module — the bot and the Mini App differ only in how they render a step, and
``session_steps.status`` is what keeps them from grading the same step twice.

The kana stage's shape comes from the plan's "within-session expanding spacing": a new syllable is
introduced, checked immediately, met again as a production drill at least five steps later, and
retested at the wrap-up. Only that last retest writes a real FSRS review. The three earlier touches
are massed repetition — they are logged with ``intra_session=True`` so the optimizer ignores them,
because repeating a character four times in fifteen minutes says nothing about how long it will last.
"""

from __future__ import annotations

import datetime as dt
import random
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.content import Item, ItemStage, ItemType, Kana
from app.db.models.learning import (
    Card,
    CardDirection,
    DailyPlan,
    LearningSession,
    SessionClient,
    SessionOutcome,
    SessionStep,
    StepKind,
    StepStatus,
    Streak,
)
from app.db.models.users import User
from app.domain import clock, distractors, interleave, session_planner, srs
from app.domain import streak as streak_domain
from app.domain.distractors import Candidate
from app.domain.grading import MatchKind, SelfGrade, grade_auto, grade_self
from app.domain.interleave import PlannedStep
from app.logging import get_logger
from app.services import card_service

log = get_logger(__name__)

CHOICE_OPTIONS = 4
KANA_STAGES = (ItemStage.kana_hira, ItemStage.kana_kata)


@dataclass(frozen=True, slots=True)
class AnswerOutcome:
    """What the caller needs to render feedback and move on."""

    accepted: bool
    correct: bool
    rating: srs.Rating | None
    correct_label: str
    match: MatchKind | None = None
    session_finished: bool = False


# --------------------------------------------------------------------------- session lifecycle


async def _load_kana(session: AsyncSession, item_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, tuple[Item, Kana]]:
    if not item_ids:
        return {}
    stmt = (
        select(Item, Kana)
        .join(Kana, (Item.ref_id == Kana.id) & (Item.type == ItemType.kana))
        .where(Item.id.in_(item_ids))
    )
    return {item.id: (item, kana) for item, kana in (await session.execute(stmt)).all()}


async def _candidate_pool(
    session: AsyncSession, *, user_id: uuid.UUID, script: str, limit: int = 200
) -> list[Candidate]:
    """Kana the learner may plausibly confuse with the target.

    Prefers syllables they have already met, as the plan requires. Early in the bootcamp that pool is
    two or three characters wide, which would make every grid trivially guessable, so it is topped up
    with the next characters in curriculum order — ones they are about to meet anyway, never random
    far-away glyphs.
    """
    seen_stmt = (
        select(Kana, Item.curriculum_order)
        .join(Item, (Item.ref_id == Kana.id) & (Item.type == ItemType.kana))
        .join(Card, (Card.item_id == Item.id) & (Card.user_id == user_id))
        .where(Kana.script == script)
        .order_by(Item.curriculum_order)
        .limit(limit)
    )
    rows = (await session.execute(seen_stmt)).all()
    pool = [Candidate(key=str(k.id), label=k.char, row=k.row) for k, _ in rows]

    if len(pool) < CHOICE_OPTIONS + 2:
        top_up = (
            select(Kana)
            .join(Item, (Item.ref_id == Kana.id) & (Item.type == ItemType.kana))
            .where(Kana.script == script)
            .order_by(Item.curriculum_order)
            .limit(limit)
        )
        known = {c.key for c in pool}
        for kana in await session.scalars(top_up):
            if str(kana.id) not in known:
                pool.append(Candidate(key=str(kana.id), label=kana.char, row=kana.row))
    return pool


def _reading_pool(pool: Sequence[Candidate], readings: dict[str, str]) -> list[Candidate]:
    """The same candidates labelled by their Cyrillic reading rather than their glyph."""
    return [Candidate(key=c.key, label=readings.get(c.key, c.label), row=c.row) for c in pool]


async def _build_planned_steps(
    session: AsyncSession,
    *,
    user: User,
    blueprint: session_planner.DailyPlanBlueprint,
    now: dt.datetime,
    rng: random.Random,
) -> list[PlannedStep]:
    """Turn the blueprint's counts into the concrete chain of steps for a kana-stage day."""
    steps: list[PlannedStep] = []

    due = await card_service.due_cards(session, user_id=user.id, now=now, limit=blueprint.review_budget)
    new_items = await card_service.next_new_items(
        session, user_id=user.id, stages=KANA_STAGES, limit=blueprint.new_items
    )

    content = await _load_kana(session, [c.item_id for c in due] + [i.id for i in new_items])
    scripts = {kana.script.value for _, kana in content.values()}
    pools = {s: await _candidate_pool(session, user_id=user.id, script=s) for s in scripts}
    readings = {str(kana.id): kana.cyrillic for _, kana in content.values()}
    # Readings for the whole pool, not just today's items: a distractor needs a label too.
    all_ids = {c.key for pool in pools.values() for c in pool}
    if all_ids:
        for kana in await session.scalars(select(Kana).where(Kana.id.in_([uuid.UUID(i) for i in all_ids]))):
            readings[str(kana.id)] = kana.cyrillic

    for card in due:
        found = content.get(card.item_id)
        if found is None:
            continue
        _, kana = found
        steps.append(
            _drill_step(
                kind=StepKind.review_recog if card.direction == CardDirection.recognition else StepKind.review_prod,
                kana=kana,
                card=card,
                pool=pools[kana.script.value],
                readings=readings,
                rng=rng,
                intra_session=False,
            )
        )

    for item in new_items:
        found = content.get(item.id)
        if found is None:
            continue
        _, kana = found
        cards = await card_service.introduce_item(session, user_id=user.id, item_id=item.id, now=now)
        by_direction = {c.direction: c for c in cards}
        recog = by_direction.get(CardDirection.recognition)
        prod = by_direction.get(CardDirection.production)
        pool = pools[kana.script.value]
        key = str(item.id)

        steps.append(
            PlannedStep(
                kind=StepKind.intro_item,
                item_key=key,
                payload={
                    "mode": "ack",
                    "item_id": key,
                    "char": kana.char,
                    "cyrillic": kana.cyrillic,
                    "mnemonic_ru": kana.mnemonic_ru,
                    "example_word": kana.example_word,
                    "example_gloss_ru": kana.example_gloss_ru,
                },
            )
        )
        if recog is not None:
            steps.append(
                _drill_step(StepKind.review_recog, kana, recog, pool, readings, rng, intra_session=True, key=key)
            )
        if prod is not None:
            steps.append(
                _drill_step(StepKind.review_prod, kana, prod, pool, readings, rng, intra_session=True, key=key)
            )
        if recog is not None:
            # The one grade of the day that reaches FSRS for this syllable.
            steps.append(
                _drill_step(
                    StepKind.wrapup, kana, recog, pool, readings, rng, intra_session=False, key=key, pinned_last=True
                )
            )
    return steps


def _drill_step(
    kind: StepKind,
    kana: Kana,
    card: Card,
    pool: Sequence[Candidate],
    readings: dict[str, str],
    rng: random.Random,
    *,
    intra_session: bool,
    key: str | None = None,
    pinned_last: bool = False,
) -> PlannedStep:
    """A multiple-choice drill in one direction.

    Recognition shows the glyph and asks for the reading; production shows the reading and asks for
    the glyph. Distractors come from the same pool either way, only the labels swap.
    """
    correct = Candidate(key=str(kana.id), label=kana.char, row=kana.row)
    produce_glyph = kind in (StepKind.review_prod,)

    if produce_glyph:
        options, index = distractors.build_choices(correct, pool, options=CHOICE_OPTIONS, rng=rng)
        prompt, labels = kana.cyrillic, [o.label for o in options]
    else:
        labelled_pool = _reading_pool(pool, readings)
        labelled_correct = Candidate(key=correct.key, label=kana.cyrillic, row=kana.row)
        options, index = distractors.build_choices(labelled_correct, labelled_pool, options=CHOICE_OPTIONS, rng=rng)
        prompt, labels = kana.char, [o.label for o in options]

    return PlannedStep(
        kind=kind,
        item_key=key or str(card.item_id),
        card_id=card.id,
        pinned_last=pinned_last,
        payload={
            "mode": "choice",
            "prompt": prompt,
            "char": kana.char,
            "cyrillic": kana.cyrillic,
            "choices": labels,
            "correct": index,
            "correct_label": labels[index],
            "intra_session": intra_session,
            "direction": card.direction.value,
        },
    )


async def start_or_resume(
    session: AsyncSession,
    *,
    user: User,
    now: dt.datetime,
    client: SessionClient = SessionClient.bot,
) -> tuple[LearningSession, bool]:
    """Today's session, creating and materialising it on first call. Returns ``(session, created)``."""
    today = clock.local_date(now, user.timezone)
    existing = await session.scalar(
        select(LearningSession).where(LearningSession.user_id == user.id, LearningSession.local_date == today)
    )
    if existing is not None:
        return existing, False

    blueprint = await _plan_for(session, user=user, now=now, today=today)
    # Not cryptography: the point is that reopening today replays the same grids and the same order.
    seed = interleave.make_seed(user.id, today)
    rng = random.Random(seed)  # noqa: S311
    planned = await _build_planned_steps(session, user=user, blueprint=blueprint, now=now, rng=rng)
    ordered = interleave.interleave(planned, seed=seed)

    learning = LearningSession(
        user_id=user.id,
        local_date=today,
        started_at=now,
        client=client,
        planned_steps=len(ordered),
        outcome=SessionOutcome.in_progress,
    )
    session.add(learning)
    await session.flush()

    for idx, step in enumerate(ordered):
        session.add(
            SessionStep(
                session_id=learning.id,
                idx=idx,
                kind=step.kind,
                card_id=step.card_id,
                item_id=uuid.UUID(step.payload["item_id"]) if "item_id" in step.payload else None,
                payload=dict(step.payload),
                status=StepStatus.pending,
            )
        )
    await session.flush()
    log.info("session created", user_id=str(user.id), date=today.isoformat(), steps=len(ordered))
    return learning, True


async def _plan_for(
    session: AsyncSession, *, user: User, now: dt.datetime, today: dt.date
) -> session_planner.DailyPlanBlueprint:
    stage = await _stage_for(session, user_id=user.id)
    due = await card_service.due_count(session, user_id=user.id, now=now)
    available = await card_service.remaining_new_count(session, user_id=user.id, stages=KANA_STAGES)
    recent = await _recent_sessions(session, user_id=user.id)
    retention = await card_service.retention_7d(session, user_id=user.id, now=now)
    blueprint = session_planner.plan_day(
        session_planner.PlannerInput(
            stage=stage,
            due_count=due,
            retention_7d=retention,
            daily_minutes_target=user.daily_minutes_target,
            missed_days=await _missed_days(session, user_id=user.id, today=today),
            recent_session_seconds=recent,
            available_new_items=available,
        )
    )
    await session.merge(
        DailyPlan(
            user_id=user.id,
            local_date=today,
            new_items_target=blueprint.new_items,
            due_count=due,
            retention_7d=retention,
            backlog_ratio=blueprint.backlog_ratio,
            plan={"section_seconds": dict(blueprint.section_seconds), "review_budget": blueprint.review_budget},
        )
    )
    return blueprint


async def _stage_for(session: AsyncSession, *, user_id: uuid.UUID) -> ItemStage:
    for stage in KANA_STAGES:
        if await card_service.remaining_new_count(session, user_id=user_id, stages=[stage]) > 0:
            return stage
    return ItemStage.core


async def _recent_sessions(session: AsyncSession, *, user_id: uuid.UUID, limit: int = 3) -> list[float]:
    stmt = (
        select(LearningSession.active_ms)
        .where(LearningSession.user_id == user_id, LearningSession.outcome == SessionOutcome.completed)
        .order_by(LearningSession.local_date.desc())
        .limit(limit)
    )
    return [float(ms) / 1000.0 for ms in await session.scalars(stmt)]


async def _missed_days(session: AsyncSession, *, user_id: uuid.UUID, today: dt.date) -> int:
    last = await session.scalar(
        select(LearningSession.local_date)
        .where(LearningSession.user_id == user_id, LearningSession.outcome == SessionOutcome.completed)
        .order_by(LearningSession.local_date.desc())
        .limit(1)
    )
    if last is None:
        return 0
    return max(0, (today - last).days - 1)


# --------------------------------------------------------------------------- serving and grading


async def next_step(session: AsyncSession, *, learning_session_id: uuid.UUID) -> SessionStep | None:
    stmt = (
        select(SessionStep)
        .where(
            SessionStep.session_id == learning_session_id,
            SessionStep.status.in_((StepStatus.pending, StepStatus.shown)),
        )
        .order_by(SessionStep.idx)
        .limit(1)
    )
    return (await session.scalars(stmt)).first()


async def mark_shown(session: AsyncSession, *, step: SessionStep, now: dt.datetime, message_id: int | None) -> None:
    if step.status == StepStatus.pending:
        step.status = StepStatus.shown
        step.shown_at = now
    if message_id is not None:
        step.tg_message_id = message_id
    await session.flush()


def _is_open(step: SessionStep) -> bool:
    """A step can be answered exactly once. Telegram redelivers callbacks; learners double-tap."""
    return step.status in (StepStatus.pending, StepStatus.shown)


async def submit_choice(
    session: AsyncSession,
    *,
    user: User,
    step: SessionStep,
    choice: int,
    now: dt.datetime,
) -> AnswerOutcome:
    """Grade a multiple-choice answer. Idempotent: a second tap is accepted=False and changes nothing."""
    correct_label = str(step.payload.get("correct_label", ""))
    if not _is_open(step):
        return AnswerOutcome(accepted=False, correct=False, rating=None, correct_label=correct_label)

    correct_index = int(step.payload.get("correct", -1))
    is_correct = choice == correct_index
    elapsed_ms = _elapsed_ms(step, now)
    rating = grade_auto(
        MatchKind.exact if is_correct else MatchKind.wrong,
        elapsed_ms=elapsed_ms,
    )

    await _apply_grade(
        session,
        user=user,
        step=step,
        rating=rating,
        now=now,
        elapsed_ms=elapsed_ms,
        payload={"choice": choice, "correct": is_correct},
    )
    return AnswerOutcome(
        accepted=True,
        correct=is_correct,
        rating=rating,
        correct_label=correct_label,
        match=MatchKind.exact if is_correct else MatchKind.wrong,
    )


async def submit_self_grade(
    session: AsyncSession,
    *,
    user: User,
    step: SessionStep,
    grade: SelfGrade,
    now: dt.datetime,
) -> AnswerOutcome:
    correct_label = str(step.payload.get("correct_label", ""))
    if not _is_open(step):
        return AnswerOutcome(accepted=False, correct=False, rating=None, correct_label=correct_label)

    elapsed_ms = _elapsed_ms(step, now)
    rating = grade_self(grade, reveal_ms=elapsed_ms)
    await _apply_grade(
        session,
        user=user,
        step=step,
        rating=rating,
        now=now,
        elapsed_ms=elapsed_ms,
        payload={"self_grade": grade.value},
    )
    return AnswerOutcome(
        accepted=True,
        correct=rating != srs.Rating.Again,
        rating=rating,
        correct_label=correct_label,
    )


async def acknowledge(session: AsyncSession, *, step: SessionStep, now: dt.datetime) -> AnswerOutcome:
    """An introduction carries no grade — the learner just says they have read it."""
    if not _is_open(step):
        return AnswerOutcome(accepted=False, correct=True, rating=None, correct_label="")
    step.status = StepStatus.answered
    step.answered_at = now
    step.result = {"acknowledged": True}
    await _bump_progress(session, step=step, now=now)
    return AnswerOutcome(accepted=True, correct=True, rating=None, correct_label="")


def _elapsed_ms(step: SessionStep, now: dt.datetime) -> int:
    if step.shown_at is None:
        return 0
    return max(0, int((now - step.shown_at).total_seconds() * 1000))


async def _apply_grade(
    session: AsyncSession,
    *,
    user: User,
    step: SessionStep,
    rating: srs.Rating,
    now: dt.datetime,
    elapsed_ms: int,
    payload: dict[str, object],
) -> None:
    step.status = StepStatus.answered
    step.answered_at = now
    step.result = {**payload, "rating": int(rating)}

    if step.card_id is not None:
        card = await session.get(Card, step.card_id)
        if card is not None:
            scheduler = srs.make_scheduler(desired_retention=float(user.desired_retention))
            result = srs.review(card_service.state_of(card), rating, now, scheduler=scheduler)
            intra = bool(step.payload.get("intra_session", False))
            # Practice steps log the grade but leave the schedule untouched; only the wrap-up retest
            # (and any genuinely due review) is allowed to move the card.
            await card_service.record_review(
                session,
                card=card,
                result=result,
                response_ms=elapsed_ms,
                auto_graded=True,
                intra_session=intra,
                answer_payload=payload,
                apply_state=not intra,
            )
    await _bump_progress(session, step=step, now=now)


async def _bump_progress(session: AsyncSession, *, step: SessionStep, now: dt.datetime) -> None:
    learning = await session.get(LearningSession, step.session_id)
    if learning is None:
        return
    learning.completed_steps += 1
    if step.shown_at is not None:
        learning.active_ms += max(0, int((now - step.shown_at).total_seconds() * 1000))
    await session.flush()


async def finish(
    session: AsyncSession, *, user: User, learning: LearningSession, now: dt.datetime, abandoned: bool = False
) -> bool:
    """Close the session and, if it earned the day, advance the streak. Returns whether it counted."""
    if learning.outcome != SessionOutcome.in_progress:
        return learning.outcome == SessionOutcome.completed

    counted = streak_domain.session_counts(
        completed_steps=learning.completed_steps,
        planned_steps=learning.planned_steps,
        active_seconds=learning.active_ms / 1000.0,
        minutes_target=user.daily_minutes_target,
    )
    learning.finished_at = now
    learning.outcome = SessionOutcome.completed if counted and not abandoned else SessionOutcome.abandoned

    if counted and not abandoned:
        await advance_streak(session, user=user, local_day=learning.local_date)
    await session.flush()
    return counted and not abandoned


async def advance_streak(session: AsyncSession, *, user: User, local_day: dt.date) -> Streak:
    row = await session.get(Streak, user.id)
    if row is None:
        row = Streak(user_id=user.id)
        session.add(row)
        await session.flush()

    state = streak_domain.StreakState(
        current=row.current,
        longest=row.longest,
        last_active_date=row.last_active_date,
        freezes_available=row.freezes_available,
        freeze_earned_week=row.freeze_earned_week,
        freeze_used_dates=tuple(row.freeze_used_dates or ()),
    )
    updated = streak_domain.register_activity(state, local_day)
    row.current = updated.current
    row.longest = updated.longest
    row.last_active_date = updated.last_active_date
    row.freezes_available = updated.freezes_available
    row.freeze_earned_week = updated.freeze_earned_week
    row.freeze_used_dates = list(updated.freeze_used_dates)
    await session.flush()
    return row
