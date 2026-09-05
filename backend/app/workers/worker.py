"""arq worker settings. Run with ``arq app.workers.worker.WorkerSettings``."""

from __future__ import annotations

from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.config import get_settings
from app.logging import configure_logging, get_logger
from app.workers import maintenance, reminders

log = get_logger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    ctx["settings"] = settings
    log.info("worker started", env=settings.env)


async def shutdown(ctx: dict[str, Any]) -> None:
    log.info("worker stopped")


class WorkerSettings:
    functions = [maintenance.ping]
    cron_jobs = [cron(reminders.reminder_tick, minute=set(range(60)), run_at_startup=False)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 10
    job_timeout = 120
    health_check_interval = 60
