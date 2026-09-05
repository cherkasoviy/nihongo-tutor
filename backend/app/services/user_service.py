"""User lookup / upsert and admin bootstrap."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserRole
from app.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TelegramIdentity:
    tg_user_id: int
    username: str | None = None
    first_name: str | None = None
    language_code: str | None = None


async def get_by_tg_id(session: AsyncSession, tg_user_id: int) -> User | None:
    result = await session.execute(select(User).where(User.tg_user_id == tg_user_id))
    return result.scalar_one_or_none()


def apply_identity(user: User, identity: TelegramIdentity, now: dt.datetime) -> None:
    user.tg_username = identity.username
    user.first_name = identity.first_name
    user.language_code = identity.language_code
    user.last_active_at = now


async def create_user(
    session: AsyncSession,
    identity: TelegramIdentity,
    *,
    role: UserRole,
    invited_by: User | None,
    daily_budget_usd: float,
    now: dt.datetime,
) -> User:
    user = User(
        tg_user_id=identity.tg_user_id,
        role=role,
        invited_by=invited_by.id if invited_by else None,
        daily_budget_usd=daily_budget_usd,
    )
    apply_identity(user, identity, now)
    session.add(user)
    await session.flush()
    return user


async def ensure_admins(session: AsyncSession, admin_tg_ids: Iterable[int]) -> int:
    """Promote already-registered users listed in ``ADMIN_TG_IDS``. Returns the number promoted.

    Users who have not started the bot yet are created as admins on their first ``/start``.
    """
    ids = list(admin_tg_ids)
    if not ids:
        return 0
    result = await session.execute(
        update(User)
        .where(User.tg_user_id.in_(ids), User.role != UserRole.admin)
        .values(role=UserRole.admin)
        .returning(User.tg_user_id)
    )
    promoted = [row[0] for row in result.all()]
    if promoted:
        log.info("admins promoted", tg_user_ids=promoted)
    return len(promoted)
