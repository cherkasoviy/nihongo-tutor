"""Shared fixtures.

Unit tests need no database. Tests marked ``integration`` get a migrated PostgreSQL from, in order:
``TEST_DATABASE_URL`` (any reachable Postgres), a testcontainers Postgres (needs Docker), or they skip.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]

TEST_BOT_TOKEN = "123456789:TEST-TOKEN-do-not-use-in-production-xx"
TEST_JWT_SECRET = "test-jwt-secret-not-for-production-0123456789abcdef"
TEST_WEBHOOK_SECRET = "test-webhook-secret-not-for-production"
ADMIN_TG_ID = 111_000_001

# Must be set before ``app.config`` is imported anywhere.
os.environ.setdefault("ENV", "test")
os.environ.setdefault("BOT_TOKEN", TEST_BOT_TOKEN)
os.environ.setdefault("JWT_SECRET", TEST_JWT_SECRET)
os.environ.setdefault("WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
os.environ.setdefault("ADMIN_TG_IDS", str(ADMIN_TG_ID))
os.environ.setdefault("MINIAPP_URL", "https://miniapp.test")
os.environ.setdefault("PUBLIC_URL", "https://api.test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")


def _docker_available() -> bool:
    try:
        import docker  # type: ignore[import-untyped]

        docker.from_env().ping()
        return True
    except Exception:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("TEST_DATABASE_URL") or _docker_available():
        return
    skip = pytest.mark.skip(reason="no PostgreSQL: set TEST_DATABASE_URL or run Docker for testcontainers")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        yield explicit
        return
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def migrated_database_url(database_url: str) -> str:
    """Run ``alembic upgrade head`` once per session and point the app at that database."""
    from app.config import get_settings
    from app.db.base import get_engine, get_sessionmaker

    os.environ["DB_URL"] = database_url
    os.environ["ALEMBIC_DB_URL"] = database_url
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return database_url


@pytest.fixture
async def db_engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    """Per-test engine; truncates learner data on teardown so tests stay independent."""
    engine = create_async_engine(migrated_database_url)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE session_steps, learning_sessions, daily_plans, streaks, review_logs, cards, "
                    "invite_redemptions, invites, ai_usage_ledger, users, items, kana CASCADE"
                )
            )
        await engine.dispose()


@pytest.fixture
async def sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def session(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as s:
        yield s


def fresh_tg_id() -> int:
    return 200_000_000 + (uuid.uuid4().int % 100_000_000)
