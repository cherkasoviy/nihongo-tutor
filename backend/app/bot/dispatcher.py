"""aiogram Bot + Dispatcher factory. The FastAPI lifespan owns the instances."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers import admin, progress, start
from app.bot.handlers import help as help_handlers
from app.bot.handlers import session as session_handlers
from app.config import Settings


def create_bot(settings: Settings, session: BaseSession | None = None) -> Bot:
    return Bot(
        token=settings.bot_token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )


def create_dispatcher(settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]) -> Dispatcher:
    dp = Dispatcher()
    # Workflow data is injected into handlers by parameter name.
    dp["settings"] = settings
    dp["sessionmaker"] = sessionmaker
    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(session_handlers.router)
    dp.include_router(progress.router)
    # Last: its catch-all for unknown commands must not shadow the routers above.
    dp.include_router(help_handlers.router)
    return dp
