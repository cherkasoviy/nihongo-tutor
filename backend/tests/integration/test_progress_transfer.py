"""Moving a learner's progress to another database.

The property under test is the one that makes this necessary at all: ``items.id`` is minted when the
seed is imported, so the same 208 syllables carry different ids in every database. Progress that
travelled by row id would arrive pointing at nothing. These tests re-mint the content ids underneath
an export and check it still lands.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content_pipeline.import_kana import import_kana
from app.db.models.content import Item, ItemType, Kana
from app.db.models.learning import Card, LearningSession, ReviewLog, Streak
from app.db.models.users import FuriganaMode, User, UserRole
from app.services import progress_transfer, session_service, user_service
from app.services.user_service import TelegramIdentity
from tests.conftest import fresh_tg_id

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 4, 1, 9, 0, tzinfo=dt.UTC)


async def _learner_with_history(sessionmaker: async_sessionmaker[AsyncSession]) -> User:
    """A learner who has actually done a lesson, so there is real FSRS state to move."""
    async with sessionmaker() as s:
        await import_kana(s)
        user = await user_service.create_user(
            s,
            TelegramIdentity(tg_user_id=fresh_tg_id(), username="anya", first_name="Аня"),
            role=UserRole.learner,
            invited_by=None,
            daily_budget_usd=0.35,
            now=NOW,
        )
        user.timezone = "Europe/Berlin"
        user.reminder_time = dt.time(20, 30)
        user.daily_new_items_target = 12
        user.furigana_mode = FuriganaMode.always
        await s.commit()
        user_id = user.id

    async with sessionmaker() as s:
        learner = await s.get(User, user_id)
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
        current = await s.get(LearningSession, learning.id)
        assert current is not None
        await session_service.finish(s, user=learner, learning=current, now=NOW)
        await s.commit()

    async with sessionmaker() as s:
        refreshed = await s.get(User, user_id)
        assert refreshed is not None
        return refreshed


async def _remint_content_ids(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """Throw the content away and import it again, so every syllable gets a brand-new id.

    This is what a fresh production database looks like from the export's point of view, and it
    cascades the learner's cards away with it — which is the point.
    """
    async with sessionmaker() as s:
        await s.execute(text("TRUNCATE kana, items CASCADE"))
        await s.commit()
    async with sessionmaker() as s:
        await import_kana(s)
        await s.commit()


async def test_progress_survives_content_ids_changing_underneath_it(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The whole reason this exists: row ids do not survive, syllables do."""
    user = await _learner_with_history(sessionmaker)

    async with sessionmaker() as s:
        payload = await progress_transfer.export_progress(s, tg_user_id=user.tg_user_id)
        before = {
            (k.script.value, k.char, c.direction.value): (c.state, c.reps, c.stability, c.due)
            for c, k in (
                await s.execute(
                    select(Card, Kana)
                    .join(Item, Item.id == Card.item_id)
                    .join(Kana, (Item.ref_id == Kana.id) & (Item.type == ItemType.kana))
                    .where(Card.user_id == user.id)
                )
            ).all()
        }
        old_item_ids = {row for row in await s.scalars(select(Item.id))}
    assert before, "the fixture must have produced cards"

    await _remint_content_ids(sessionmaker)

    async with sessionmaker() as s:
        new_item_ids = {row for row in await s.scalars(select(Item.id))}
        surviving = int(
            (await s.execute(select(func.count()).select_from(Card).where(Card.user_id == user.id))).scalar_one()
        )
    assert not (old_item_ids & new_item_ids), "re-importing must mint fresh ids"
    assert surviving == 0, "and the cards went with the old content"

    async with sessionmaker() as s:
        report = await progress_transfer.import_progress(s, payload)
        await s.commit()

    assert report.cards == len(before)
    assert report.skipped_unknown_syllables == 0

    async with sessionmaker() as s:
        after = {
            (k.script.value, k.char, c.direction.value): (c.state, c.reps, c.stability, c.due)
            for c, k in (
                await s.execute(
                    select(Card, Kana)
                    .join(Item, Item.id == Card.item_id)
                    .join(Kana, (Item.ref_id == Kana.id) & (Item.type == ItemType.kana))
                    .where(Card.user_id == user.id)
                )
            ).all()
        }
    assert after == before, "every syllable's FSRS state came back intact"


async def test_settings_streak_and_sessions_travel_too(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await _learner_with_history(sessionmaker)

    async with sessionmaker() as s:
        payload = await progress_transfer.export_progress(s, tg_user_id=user.tg_user_id)
        streak_before = await s.get(Streak, user.id)
        assert streak_before is not None
        expected_streak = streak_before.current
        sessions_before = int(
            (
                await s.execute(
                    select(func.count()).select_from(LearningSession).where(LearningSession.user_id == user.id)
                )
            ).scalar_one()
        )

    await _remint_content_ids(sessionmaker)
    async with sessionmaker() as s:
        await s.execute(text("TRUNCATE users CASCADE"))  # a genuinely fresh destination
        await s.commit()

    async with sessionmaker() as s:
        await progress_transfer.import_progress(s, payload)
        await s.commit()

    async with sessionmaker() as s:
        landed = await user_service.get_by_tg_id(s, user.tg_user_id)
        assert landed is not None
        assert landed.timezone == "Europe/Berlin"
        assert landed.reminder_time == dt.time(20, 30), "a learner should not have to set this twice"
        assert landed.daily_new_items_target == 12
        assert landed.furigana_mode is FuriganaMode.always
        streak = await s.get(Streak, landed.id)
        assert streak is not None and streak.current == expected_streak
        sessions = int(
            (
                await s.execute(
                    select(func.count()).select_from(LearningSession).where(LearningSession.user_id == landed.id)
                )
            ).scalar_one()
        )
    assert sessions == sessions_before


async def test_applying_the_same_export_twice_changes_nothing(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """It has to be re-runnable: a learner keeps using the old instance until the cutover."""
    user = await _learner_with_history(sessionmaker)
    async with sessionmaker() as s:
        payload = await progress_transfer.export_progress(s, tg_user_id=user.tg_user_id)

    async def counts() -> tuple[int, int, int]:
        async with sessionmaker() as s:
            cards = int((await s.execute(select(func.count()).select_from(Card))).scalar_one())
            reviews = int((await s.execute(select(func.count()).select_from(ReviewLog))).scalar_one())
            sessions = int((await s.execute(select(func.count()).select_from(LearningSession))).scalar_one())
            return cards, reviews, sessions

    async with sessionmaker() as s:
        await progress_transfer.import_progress(s, payload)
        await s.commit()
    once = await counts()

    async with sessionmaker() as s:
        second = await progress_transfer.import_progress(s, payload)
        await s.commit()
    twice = await counts()

    assert once == twice, "a second run must not duplicate anything"
    assert second.reviews == 0 and second.sessions == 0


async def test_a_syllable_the_destination_does_not_have_is_reported_not_dropped(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Importing before the seed would otherwise lose progress quietly."""
    user = await _learner_with_history(sessionmaker)
    async with sessionmaker() as s:
        payload = await progress_transfer.export_progress(s, tg_user_id=user.tg_user_id)

    async with sessionmaker() as s:
        await s.execute(text("TRUNCATE kana, items CASCADE"))
        await s.commit()

    async with sessionmaker() as s:
        report = await progress_transfer.import_progress(s, payload)
        await s.commit()

    assert report.cards == 0
    assert report.skipped_unknown_syllables == len(payload["cards"]) > 0


async def test_an_export_from_the_future_is_refused(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user = await _learner_with_history(sessionmaker)
    async with sessionmaker() as s:
        payload = await progress_transfer.export_progress(s, tg_user_id=user.tg_user_id)
    payload["version"] = progress_transfer.FORMAT_VERSION + 1

    async with sessionmaker() as s:
        with pytest.raises(ValueError, match="unsupported export version"):
            await progress_transfer.import_progress(s, payload)


async def test_exporting_an_unknown_learner_is_an_error_not_an_empty_file(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as s:
        with pytest.raises(LookupError, match="no learner"):
            await progress_transfer.export_progress(s, tg_user_id=1)


async def test_the_export_carries_no_row_ids(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A guard on the format itself: an id in here would be a trap for the next person."""
    user = await _learner_with_history(sessionmaker)
    async with sessionmaker() as s:
        payload = await progress_transfer.export_progress(s, tg_user_id=user.tg_user_id)

    for card in payload["cards"]:
        assert "item_id" not in card and "id" not in card and "card_id" not in card
        assert {"script", "char", "direction"} <= set(card), "syllable identity must be self-describing"
    assert all("card_id" not in r and "id" not in r for c in payload["cards"] for r in c["reviews"])


async def test_review_history_arrives_intact(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The optimizer's input in Phase 4; losing it would cost the learner their fitted parameters."""
    user = await _learner_with_history(sessionmaker)
    async with sessionmaker() as s:
        payload = await progress_transfer.export_progress(s, tg_user_id=user.tg_user_id)
        before = sorted(
            (row.rating, row.review_at, row.intra_session, row.state_before.value)
            for row in await s.scalars(
                select(ReviewLog).join(Card, Card.id == ReviewLog.card_id).where(Card.user_id == user.id)
            )
        )
    assert before

    await _remint_content_ids(sessionmaker)
    async with sessionmaker() as s:
        await progress_transfer.import_progress(s, payload)
        await s.commit()

    async with sessionmaker() as s:
        after = sorted(
            (row.rating, row.review_at, row.intra_session, row.state_before.value)
            for row in await s.scalars(
                select(ReviewLog).join(Card, Card.id == ReviewLog.card_id).where(Card.user_id == user.id)
            )
        )
    assert after == before
