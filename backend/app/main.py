"""FastAPI application factory.

Mounts ``/api`` (Mini App), ``/tg/webhook`` (Telegram) and ``/healthz``. The lifespan owns the
aiogram ``Bot``/``Dispatcher`` pair, registers the webhook and bootstraps admins.
"""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from sqlalchemy import text

from app import __version__
from app.api.router import api_router
from app.api.schemas import HealthOut
from app.bot.dispatcher import create_bot, create_dispatcher
from app.config import Settings, get_settings
from app.db.base import get_engine, get_sessionmaker
from app.logging import configure_logging, get_logger
from app.services import user_service

log = get_logger(__name__)


def _validate_prod_settings(settings: Settings) -> None:
    if not settings.is_prod:
        return
    missing = [
        name
        for name, value in (
            ("BOT_TOKEN", settings.bot_token.get_secret_value()),
            ("WEBHOOK_SECRET", settings.webhook_secret.get_secret_value()),
            ("JWT_SECRET", settings.jwt_secret.get_secret_value()),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"missing required settings in prod: {', '.join(missing)}")
    if len(settings.jwt_secret.get_secret_value()) < 32:
        raise RuntimeError("JWT_SECRET must be at least 32 characters (openssl rand -hex 32)")
    if not settings.public_url.startswith("https://"):
        raise RuntimeError("PUBLIC_URL must be https in prod (Telegram webhooks require TLS)")


async def _setup_webhook(bot: Bot, settings: Settings) -> None:
    info = await bot.get_webhook_info()
    if info.url == settings.webhook_url:
        log.info("webhook already set", url=info.url)
        return
    await bot.set_webhook(
        url=settings.webhook_url,
        secret_token=settings.webhook_secret.get_secret_value() or None,
        drop_pending_updates=False,
        allowed_updates=["message", "callback_query"],
    )
    log.info("webhook set", url=settings.webhook_url)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    _validate_prod_settings(settings)

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await user_service.ensure_admins(session, settings.admin_tg_ids)
        await session.commit()

    bot: Bot | None = None
    dp: Dispatcher | None = None
    if settings.bot_token.get_secret_value():
        bot = create_bot(settings)
        dp = create_dispatcher(settings, sessionmaker)
        me = await bot.get_me()
        app.state.bot_username = me.username
        if settings.public_url.startswith("https://"):
            await _setup_webhook(bot, settings)
        else:
            log.warning("PUBLIC_URL is not https; webhook not registered", public_url=settings.public_url)
        await dp.emit_startup(bot=bot)
    else:
        log.warning("BOT_TOKEN is empty; Telegram integration disabled")

    app.state.bot = bot
    app.state.dispatcher = dp
    try:
        yield
    finally:
        if dp is not None and bot is not None:
            await dp.emit_shutdown(bot=bot)
            await bot.session.close()
        await get_engine().dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Nihongo Tutor API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if not settings.is_prod else None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.bot = None
    app.state.dispatcher = None
    app.state.bot_username = None
    app.include_router(api_router)

    @app.get("/healthz", response_model=HealthOut, tags=["ops"])
    async def healthz() -> HealthOut:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return HealthOut(status="ok", env=settings.env, version=__version__)

    @app.post(settings.webhook_path, include_in_schema=False)
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> Response:
        expected = settings.webhook_secret.get_secret_value()
        if expected and not hmac.compare_digest(x_telegram_bot_api_secret_token or "", expected):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="bad secret token")
        bot: Bot | None = request.app.state.bot
        dp: Dispatcher | None = request.app.state.dispatcher
        if bot is None or dp is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="bot not configured")
        payload: dict[str, Any] = await request.json()
        update = Update.model_validate(payload, context={"bot": bot})
        await dp.feed_webhook_update(bot, update)
        return Response(status_code=status.HTTP_200_OK)

    return app
