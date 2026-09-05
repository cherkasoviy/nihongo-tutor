from __future__ import annotations

from typing import Any

from app.logging import get_logger

log = get_logger(__name__)


async def ping(ctx: dict[str, Any], payload: str = "pong") -> str:
    """Smoke-test job used by ``make worker-ping`` and the compose health check."""
    log.info("ping", payload=payload)
    return payload
