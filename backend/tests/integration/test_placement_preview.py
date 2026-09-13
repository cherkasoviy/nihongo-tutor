"""Claiming a batch of syllables should be a decision the learner can see before making it."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.content_pipeline.import_kana import import_kana
from app.db.models.content import Item, ItemType
from app.db.models.users import User, UserRole
from app.services import card_service, placement_service, user_service
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
        rows = await s.scalars(
            select(Item.id).where(Item.type == ItemType.kana).order_by(Item.curriculum_order).limit(n)
        )
        return list(rows)


async def test_the_preview_matches_what_claiming_actually_does(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """If these two disagree, the learner agreed to something that did not happen."""
    user = await _learner(sessionmaker)
    ids = await _kana_ids(sessionmaker, 46)

    async with sessionmaker() as s:
        shown = await placement_service.preview(s, user_id=user.id, item_ids=ids)

    async with sessionmaker() as s:
        applied = await placement_service.mark_known(s, user_id=user.id, item_ids=ids, now=NOW)
        await s.commit()

    assert shown.cards == applied.seeded
    assert shown.already_tested == applied.skipped_already_reviewed
    assert shown.syllables == 46
    assert shown.days == -(-shown.cards // shown.per_day)


async def test_the_preview_changes_nothing(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    user = await _learner(sessionmaker)
    ids = await _kana_ids(sessionmaker, 10)
    async with sessionmaker() as s:
        await placement_service.preview(s, user_id=user.id, item_ids=ids)
        await s.commit()
    async with sessionmaker() as s:
        assert await placement_service.claimed_but_unverified(s, user_id=user.id) == 0


async def test_a_claimed_batch_is_visible_as_work_coming_back(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The finished screen reads as a dead end unless it can say when something returns. After a
    bulk claim there is plenty queued — the learner just cannot see it."""
    user = await _learner(sessionmaker)
    ids = await _kana_ids(sessionmaker, 46)
    async with sessionmaker() as s:
        await placement_service.mark_known(s, user_id=user.id, item_ids=ids, now=NOW)
        await s.commit()

    async with sessionmaker() as s:
        tomorrow, next_due = await card_service.upcoming(s, user_id=user.id, now=NOW, timezone="Asia/Tokyo")
    assert tomorrow > 0, "a learner who just claimed 46 syllables must be told something returns"
    assert next_due is not None and next_due > NOW
