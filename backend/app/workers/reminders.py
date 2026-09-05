"""Reminder cron (Phase 1 fills this in: select users whose local time matches ``reminder_time``)."""

from __future__ import annotations

from typing import Any

from app.logging import get_logger

log = get_logger(__name__)


async def reminder_tick(ctx: dict[str, Any]) -> int:
    """Runs every minute. Returns the number of reminders sent (always 0 in Phase 0)."""
    log.debug("reminder tick")
    return 0
