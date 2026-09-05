"""Shared bot fixtures.

aiogram routers are module-level singletons and can be attached to exactly one Dispatcher, so the
Dispatcher is session-scoped and every test swaps its ``sessionmaker`` instead of building a new one.
"""

from __future__ import annotations

import pytest
from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.dispatcher import create_dispatcher
from app.config import get_settings
from tests.helpers.mocked_bot import MockedBot


@pytest.fixture
def bot() -> MockedBot:
    return MockedBot()


@pytest.fixture(scope="session")
def _dispatcher(migrated_database_url: str) -> Dispatcher:
    return create_dispatcher(get_settings(), sessionmaker=None)  # type: ignore[arg-type]


@pytest.fixture
def dp(_dispatcher: Dispatcher, sessionmaker: async_sessionmaker[AsyncSession]) -> Dispatcher:
    _dispatcher["sessionmaker"] = sessionmaker
    return _dispatcher
