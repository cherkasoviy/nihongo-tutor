"""Placement: claiming syllables you already know, and having the claim checked.

Two learners share this curriculum from opposite ends — a complete beginner and someone who already
reads the gojūon. Without placement the second one spends three weeks on a formality.

The design under test is that a claim is *seeded, not skipped*: it is written as the state the real
scheduler produces for a card answered correctly twice, with the due date pulled into a spread
window, so every claim is verified within a couple of weeks instead of being trusted.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content_pipeline.import_kana import import_kana
from app.db.models.content import Item, ItemStage, ItemType, Kana, KanaKind, KanaScript
from app.db.models.learning import Card, CardDirection, CardState, ReviewLog, SessionStep, StepKind
from app.db.models.users import User, UserRole
from app.domain import srs
from app.services import card_service, placement_service, session_service, stats_service, user_service
from app.services.session_service import KANA_STAGES
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
        await s.commit()
        return user


async def _basic_gojuon(sessionmaker: async_sessionmaker[AsyncSession]) -> list[Item]:
    """What "she knows the basic gojūon" means: 46 per script, both scripts."""
    async with sessionmaker() as s:
        return list(
            await s.scalars(
                select(Item)
                .join(Kana, (Item.ref_id == Kana.id) & (Item.type == ItemType.kana))
                .where(Kana.kind == KanaKind.basic)
                .order_by(Item.curriculum_order)
            )
        )


async def test_claiming_the_gojuon_skips_it_from_teaching_but_not_from_checking(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await _learner(sessionmaker)
    known = await _basic_gojuon(sessionmaker)
    assert len(known) == 92, "46 basic syllables in each script"

    async with sessionmaker() as s:
        before = await card_service.remaining_new_count(s, user_id=user.id, stages=KANA_STAGES)
        result = await placement_service.mark_known(s, user_id=user.id, item_ids=[i.id for i in known], now=NOW)
        await s.commit()
        after = await card_service.remaining_new_count(s, user_id=user.id, stages=KANA_STAGES)

    assert before == 208
    assert result.seeded == 92 * 2, "both directions of each claimed syllable"
    assert after == 208 - 92, "the claimed syllables leave the teaching queue"


async def test_a_claim_is_seeded_as_the_scheduler_would_have_left_it(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Not an invented state: exactly what two correct answers produce."""
    user = await _learner(sessionmaker)
    known = (await _basic_gojuon(sessionmaker))[:5]

    scheduler = srs.make_scheduler(enable_fuzzing=False)
    expected = srs.new_state(NOW)
    for _ in range(2):
        expected = srs.review(expected, srs.Rating.Good, NOW, scheduler=scheduler).state

    async with sessionmaker() as s:
        await placement_service.mark_known(s, user_id=user.id, item_ids=[i.id for i in known], now=NOW)
        await s.commit()
        cards = list(await s.scalars(select(Card).where(Card.user_id == user.id)))

    assert cards
    for card in cards:
        assert card.state is CardState.review
        assert card.stability == pytest.approx(expected.stability)
        assert card.difficulty == pytest.approx(expected.difficulty)
        assert card.lapses == 0
        assert card.due > NOW, "claimed syllables are checked later, not drilled immediately"


async def test_claims_are_spread_so_they_do_not_all_come_due_at_once(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A hundred claims landing on one day would bury the learner and gate their new items to zero."""
    user = await _learner(sessionmaker)
    known = await _basic_gojuon(sessionmaker)

    async with sessionmaker() as s:
        await placement_service.mark_known(s, user_id=user.id, item_ids=[i.id for i in known], now=NOW)
        await s.commit()
        per_day: dict[dt.date, int] = {}
        for card in await s.scalars(select(Card).where(Card.user_id == user.id)):
            per_day[card.due.date()] = per_day.get(card.due.date(), 0) + 1

    assert len(per_day) > 1, "the whole claim must not land on a single day"
    busiest = max(per_day.values())
    assert busiest <= placement_service.SEEDED_REVIEWS_PER_DAY, f"{busiest} on one day"
    # And every claim is checked soon: self-report goes stale, so the window stays weeks not months.
    assert max(per_day) - NOW.date() <= dt.timedelta(days=30)


async def test_a_claimed_syllable_comes_back_as_free_recall_not_a_grid(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The pay-off of seeding into `review`: her first sight of it is a real test, not a quiz."""
    user = await _learner(sessionmaker)
    known = (await _basic_gojuon(sessionmaker))[:5]

    async with sessionmaker() as s:
        await placement_service.mark_known(s, user_id=user.id, item_ids=[i.id for i in known], now=NOW)
        await s.commit()

    later = NOW + dt.timedelta(days=3)
    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        learning, _ = await session_service.start_or_resume(s, user=learner, now=later)
        await s.commit()
        steps = list(await s.scalars(select(SessionStep).where(SessionStep.session_id == learning.id)))

    recall = [st for st in steps if st.payload.get("mode") == "self"]
    assert recall, [st.payload.get("mode") for st in steps]
    assert all(st.kind is StepKind.review_recog for st in recall)


async def test_a_wrong_claim_is_caught_by_the_scheduler(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The whole reason claims are seeded rather than skipped."""
    user = await _learner(sessionmaker)
    known = (await _basic_gojuon(sessionmaker))[:1]

    async with sessionmaker() as s:
        await placement_service.mark_known(s, user_id=user.id, item_ids=[i.id for i in known], now=NOW)
        await s.commit()
        card = (
            await s.scalars(select(Card).where(Card.user_id == user.id, Card.direction == CardDirection.recognition))
        ).one()
        before_stability = card.stability
        assert before_stability is not None

        # She turns out not to know it after all.
        scheduler = srs.make_scheduler(desired_retention=float(user.desired_retention))
        result = srs.review(card_service.state_of(card), srs.Rating.Again, card.due, scheduler=scheduler)
        await card_service.record_review(
            s, card=card, result=result, response_ms=4000, auto_graded=False, intra_session=False
        )
        await s.commit()
        await s.refresh(card)

    assert card.state is CardState.relearning
    assert card.lapses == 1
    assert card.stability is not None and card.stability < before_stability


async def test_stats_separate_what_was_claimed_from_what_was_proven(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Showing 90% mastery because someone ticked a box is the app flattering them."""
    user = await _learner(sessionmaker)
    known = await _basic_gojuon(sessionmaker)

    async with sessionmaker() as s:
        await placement_service.mark_known(s, user_id=user.id, item_ids=[i.id for i in known], now=NOW)
        await s.commit()
        stats = await stats_service.learner_stats(s, user_id=user.id, now=NOW)

    assert stats.kana_known == 0, "nothing is proven until the learner answers something"
    assert stats.kana_claimed == 92, "counted in syllables, so it is comparable with kana_known"
    assert stats.kana_introduced == 92


async def test_a_claim_never_overwrites_answers_the_learner_actually_gave(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Claiming a syllable already being learned must not wipe its real scheduling history."""
    user = await _learner(sessionmaker)

    async with sessionmaker() as s:
        learner = await s.get(User, user.id)
        assert learner is not None
        learning, _ = await session_service.start_or_resume(s, user=learner, now=NOW)
        await s.commit()
        for _ in range(400):
            step = await session_service.next_step(s, learning_session_id=learning.id)
            if step is None:
                break
            await session_service.mark_shown(s, step=step, now=NOW, message_id=None)
            if step.payload.get("mode") == "ack":
                await session_service.acknowledge(s, step=step, now=NOW)
            else:
                await session_service.submit_choice(
                    s, user=learner, step=step, choice=int(step.payload["correct"]), now=NOW
                )
        await s.commit()
        taught_items = list(await s.scalars(select(Card.item_id).where(Card.user_id == user.id).distinct()))
        before = {
            c.id: (c.state, c.stability, c.due, c.reps)
            for c in await s.scalars(select(Card).where(Card.user_id == user.id))
        }
        reviewed = {
            cid
            for cid in before
            if (await s.scalars(select(func.count()).select_from(ReviewLog).where(ReviewLog.card_id == cid))).one()
        }

        result = await placement_service.mark_known(s, user_id=user.id, item_ids=taught_items, now=NOW)
        await s.commit()
        after = {
            c.id: (c.state, c.stability, c.due, c.reps)
            for c in await s.scalars(select(Card).where(Card.user_id == user.id))
        }

    assert result.skipped_already_reviewed > 0, "the lesson must have produced answered cards"
    for card_id in reviewed:
        assert after[card_id] == before[card_id], "an answered card is history and must be left alone"


async def test_taking_back_an_untested_claim_puts_the_syllable_back_in_the_queue(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await _learner(sessionmaker)
    known = (await _basic_gojuon(sessionmaker))[:10]
    ids = [i.id for i in known]

    async with sessionmaker() as s:
        before = await card_service.remaining_new_count(s, user_id=user.id, stages=KANA_STAGES)
        await placement_service.mark_known(s, user_id=user.id, item_ids=ids, now=NOW)
        await s.commit()
        assert await card_service.remaining_new_count(s, user_id=user.id, stages=KANA_STAGES) == before - 10

        result = await placement_service.unmark_known(s, user_id=user.id, item_ids=ids)
        await s.commit()
        after = await card_service.remaining_new_count(s, user_id=user.id, stages=KANA_STAGES)

    assert result.cleared == 10 * 2
    assert after == before, "an untested claim leaves no trace once withdrawn"


async def test_marking_the_same_claim_twice_changes_nothing(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await _learner(sessionmaker)
    ids = [i.id for i in (await _basic_gojuon(sessionmaker))[:8]]

    async with sessionmaker() as s:
        await placement_service.mark_known(s, user_id=user.id, item_ids=ids, now=NOW)
        await s.commit()
        first = sorted(
            (c.item_id, c.direction, c.due, c.stability)
            for c in await s.scalars(select(Card).where(Card.user_id == user.id))
        )
        await placement_service.mark_known(s, user_id=user.id, item_ids=ids, now=NOW)
        await s.commit()
        second = sorted(
            (c.item_id, c.direction, c.due, c.stability)
            for c in await s.scalars(select(Card).where(Card.user_id == user.id))
        )
    assert first == second


async def test_claiming_nothing_is_harmless(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    user = await _learner(sessionmaker)
    async with sessionmaker() as s:
        assert (await placement_service.mark_known(s, user_id=user.id, item_ids=[], now=NOW)).seeded == 0
        assert (await placement_service.unmark_known(s, user_id=user.id, item_ids=[])).cleared == 0


async def test_katakana_can_be_claimed_independently(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """She reads hiragana more fluently than katakana; the two must be separable."""
    user = await _learner(sessionmaker)
    async with sessionmaker() as s:
        hira = list(
            await s.scalars(
                select(Item.id)
                .join(Kana, (Item.ref_id == Kana.id) & (Item.type == ItemType.kana))
                .where(Kana.script == KanaScript.hiragana, Kana.kind == KanaKind.basic)
            )
        )
        await placement_service.mark_known(s, user_id=user.id, item_ids=hira, now=NOW)
        await s.commit()
        remaining_hira = await card_service.remaining_new_count(s, user_id=user.id, stages=[ItemStage.kana_hira])
        remaining_kata = await card_service.remaining_new_count(s, user_id=user.id, stages=[ItemStage.kana_kata])
    assert remaining_hira == 104 - 46
    assert remaining_kata == 104, "claiming hiragana must not touch katakana"
