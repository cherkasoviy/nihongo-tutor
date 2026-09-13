"""Un-claiming a syllable in the middle of a lesson must not destroy the lesson.

Reconstructed from production. On 13 September a learner's daily sitting was recorded as 40
planned steps with 10 completed and an outcome of "completed". Only 10 `session_steps` rows
survived, every one an `intro_item` — the only kind built without a `card_id`. The cause:
`unmark_known` deleted untested cards, `session_steps.card_id` was ON DELETE CASCADE, and the
pending steps of the live sitting went with them. With nothing left pending, `next_step` returned
None and the sitting closed itself.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content_pipeline.import_kana import import_kana
from app.db.models.content import Item, ItemType
from app.db.models.learning import (
    Card,
    CardState,
    LearningSession,
    SessionStep,
    StepKind,
    StepStatus,
)
from app.db.models.users import User, UserRole
from app.services import card_service, placement_service, session_service, user_service
from app.services.user_service import TelegramIdentity
from tests.conftest import fresh_tg_id

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 4, 1, 9, 0, tzinfo=dt.UTC)


async def _learner(sessionmaker: async_sessionmaker[AsyncSession]) -> User:
    async with sessionmaker() as s:
        await import_kana(s)
        user = await user_service.create_user(
            s,
            TelegramIdentity(tg_user_id=fresh_tg_id(), first_name="Аня"),
            role=UserRole.learner,
            invited_by=None,
            daily_budget_usd=0.35,
            now=NOW,
        )
        user.timezone = "Asia/Tokyo"
        await s.commit()
        return user


async def _kana_ids(sessionmaker: async_sessionmaker[AsyncSession], n: int) -> list:
    async with sessionmaker() as s:
        return list(
            await s.scalars(select(Item.id).where(Item.type == ItemType.kana).order_by(Item.curriculum_order).limit(n))
        )


async def test_unclaiming_mid_lesson_does_not_delete_the_lesson(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The production failure, reproduced end to end.

    The trigger is not obvious. The kana grid marks a syllable "introduced" as soon as a card
    exists — which ``introduce_item`` does the moment the lesson decides to teach it — and in
    placement mode tapping an introduced cell sends ``known: false``. So tapping a syllable the
    running lesson had *just taught* un-claimed it, and the cards it deleted were the ones that
    lesson's own pending steps referenced.
    """
    user = await _learner(sessionmaker)

    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        learning, _ = await session_service.start_or_resume(s, user=learner, now=NOW)
        await s.commit()
        learning_id, planned_before = learning.id, learning.planned_steps

    async with sessionmaker() as s:
        rows = list(await s.scalars(select(SessionStep).where(SessionStep.session_id == learning_id)))
        taught = sorted({r.item_id for r in rows if r.item_id is not None})
        steps_before = len(rows)
        card_backed = sum(1 for r in rows if r.card_id is not None)
    assert steps_before == planned_before > 0
    assert card_backed > 0, "the sitting must contain card-backed steps for this to mean anything"

    # She opens Кана mid-sitting and taps a syllable the lesson has just introduced.
    async with sessionmaker() as s:
        result = await placement_service.unmark_known(s, user_id=user.id, item_ids=taught, now=NOW)
        await s.commit()
    assert result.cleared > 0, "the un-claim has to actually do something for this test to mean anything"

    async with sessionmaker() as s:
        steps_after = int(
            (
                await s.execute(
                    select(func.count()).select_from(SessionStep).where(SessionStep.session_id == learning_id)
                )
            ).scalar_one()
        )
    assert steps_after == steps_before, f"{steps_before - steps_after} steps were destroyed by an un-claim"

    # And the sitting is still live rather than having closed itself.
    async with sessionmaker() as s:
        step = await session_service.next_step(s, learning_session_id=learning_id)
        await s.commit()
    assert step is not None, "the lesson closed itself after the un-claim"


async def test_unclaiming_returns_the_syllable_to_the_teaching_queue(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The whole point of the old delete. A reset has to achieve the same thing."""
    user = await _learner(sessionmaker)
    ids = await _kana_ids(sessionmaker, 5)

    async with sessionmaker() as s:
        await placement_service.mark_known(s, user_id=user.id, item_ids=ids, now=NOW)
        await s.commit()
    async with sessionmaker() as s:
        taught_while_claimed = await card_service.remaining_new_count(
            s, user_id=user.id, stages=list(session_service.KANA_STAGES)
        )

    async with sessionmaker() as s:
        await placement_service.unmark_known(s, user_id=user.id, item_ids=ids, now=NOW)
        await s.commit()

    async with sessionmaker() as s:
        remaining = await card_service.remaining_new_count(s, user_id=user.id, stages=list(session_service.KANA_STAGES))
        cards = list(await s.scalars(select(Card).where(Card.user_id == user.id, Card.item_id.in_(ids))))

    assert remaining == taught_while_claimed + 5, "un-claimed syllables must be owed to the learner again"
    assert cards, "the cards should be reset, not deleted"
    assert all(c.state is CardState.new and c.reps == 0 and c.stability is None for c in cards)


async def test_a_card_deleted_anyway_leaves_the_step_but_skips_it(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Defence in depth: nothing in the app deletes cards any more, but the schema must not lose
    the record if something ever does, and the queue must not stall on an unanswerable step.

    Skipping is lazy — ``next_step`` only examines steps up to the first one it can return — so the
    state that matters is the one at the end, which is what ``finish`` and the streak read.
    """
    user = await _learner(sessionmaker)
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        learning, _ = await session_service.start_or_resume(s, user=learner, now=NOW)
        await s.commit()
        learning_id, planned_before = learning.id, learning.planned_steps

    async with sessionmaker() as s:
        card_ids = list(await s.scalars(select(Card.id).where(Card.user_id == user.id)))
        await s.execute(Card.__table__.delete().where(Card.id.in_(card_ids)))
        await s.commit()

    async with sessionmaker() as s:
        rows = list(await s.scalars(select(SessionStep).where(SessionStep.session_id == learning_id)))
    assert len(rows) == planned_before, "SET NULL must keep the record of what was shown"
    assert any(
        r.card_id is None and r.kind is not StepKind.intro_item for r in rows
    ), "the scenario needs at least one card-backed step to have been orphaned"

    # Work the sitting to its end; every step still offered must be answerable.
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        for _ in range(400):
            step = await session_service.next_step(s, learning_session_id=learning_id)
            if step is None:
                break
            assert (
                step.card_id is not None or step.kind in session_service.CARDLESS_STEP_KINDS
            ), "an unanswerable step was offered to the learner"
            await session_service.mark_shown(s, step=step, now=NOW, message_id=None)
            await session_service.acknowledge(s, step=step, now=NOW)
        await s.commit()

    async with sessionmaker() as s:
        row = await s.get(LearningSession, learning_id)
        assert row is not None
        orphaned = list(
            await s.scalars(
                select(SessionStep).where(
                    SessionStep.session_id == learning_id, SessionStep.status == StepStatus.skipped
                )
            )
        )
    assert orphaned, "an unanswerable step must be marked skipped, not left pending forever"
    assert row.planned_steps == planned_before - len(
        orphaned
    ), "planned_steps must keep describing work the learner could actually do"
    assert (
        row.planned_steps == row.completed_steps
    ), "everything still answerable was answered, so the ratio must read as a finished lesson"


async def test_an_orphaned_wrapup_is_skipped_not_served(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The wrap-up carries a card, so it must not be on the card-less allow-list.

    If it is, layer 3 switches off for precisely the step that matters most: the wrap-up is the one
    grade of the day that reaches FSRS for a syllable, and `_apply_grade` guards with
    `if step.card_id is not None`. An orphaned wrap-up would be shown, answered, marked answered,
    counted in completed_steps and told «Верно ✓» — with the grade silently discarded.
    """
    assert (
        StepKind.wrapup not in session_service.CARDLESS_STEP_KINDS
    ), "wrapup is built through _drill_step, which always sets card_id"

    user = await _learner(sessionmaker)
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        learning, _ = await session_service.start_or_resume(s, user=learner, now=NOW)
        await s.commit()
        learning_id = learning.id

    async with sessionmaker() as s:
        wrapups = list(
            await s.scalars(
                select(SessionStep).where(SessionStep.session_id == learning_id, SessionStep.kind == StepKind.wrapup)
            )
        )
    assert wrapups, "the sitting must contain a wrap-up for this test to mean anything"
    assert all(w.card_id is not None for w in wrapups), "a wrap-up is built with a card"

    # Orphan every card, as a delete would under 0006's SET NULL.
    async with sessionmaker() as s:
        await s.execute(
            SessionStep.__table__.update().where(SessionStep.session_id == learning_id).values(card_id=None)
        )
        await s.commit()

    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        for _ in range(400):
            step = await session_service.next_step(s, learning_session_id=learning_id)
            if step is None:
                break
            assert step.kind is not StepKind.wrapup, "an orphaned wrap-up was served to the learner"
            await session_service.mark_shown(s, step=step, now=NOW, message_id=None)
            await session_service.acknowledge(s, step=step, now=NOW)
        await s.commit()

    async with sessionmaker() as s:
        skipped_wrapups = list(
            await s.scalars(
                select(SessionStep).where(
                    SessionStep.session_id == learning_id,
                    SessionStep.kind == StepKind.wrapup,
                    SessionStep.status == StepStatus.skipped,
                )
            )
        )
    assert len(skipped_wrapups) == len(wrapups), "every orphaned wrap-up must be skipped"
