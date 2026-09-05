"""What happens on ``/start``: returning user, admin bootstrap, or invite redemption."""

from __future__ import annotations

import datetime as dt
import enum
from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserRole
from app.services import invite_service, user_service
from app.services.invite_service import InviteError, InviteFailure
from app.services.user_service import TelegramIdentity


class StartOutcome(enum.StrEnum):
    returning = "returning"
    admin_created = "admin_created"
    invited = "invited"
    needs_code = "needs_code"
    invite_invalid = "invite_invalid"


@dataclass(frozen=True, slots=True)
class StartResult:
    outcome: StartOutcome
    user: User | None = None
    failure: InviteFailure | None = None


async def handle_start(
    session: AsyncSession,
    *,
    identity: TelegramIdentity,
    code: str | None,
    admin_tg_ids: Collection[int],
    daily_budget_usd: float,
    now: dt.datetime | None = None,
) -> StartResult:
    """Resolve a ``/start`` command. Commits on success paths that create or touch a user."""
    now = now or dt.datetime.now(dt.UTC)
    is_admin = identity.tg_user_id in set(admin_tg_ids)

    existing = await user_service.get_by_tg_id(session, identity.tg_user_id)
    if existing is not None:
        user_service.apply_identity(existing, identity, now)
        if is_admin and existing.role != UserRole.admin:
            existing.role = UserRole.admin
        await session.commit()
        return StartResult(StartOutcome.returning, user=existing)

    if is_admin:
        user = await user_service.create_user(
            session, identity, role=UserRole.admin, invited_by=None, daily_budget_usd=daily_budget_usd, now=now
        )
        user.onboarded_at = now
        await session.commit()
        return StartResult(StartOutcome.admin_created, user=user)

    if not code:
        return StartResult(StartOutcome.needs_code)

    try:
        user = await invite_service.redeem(
            session, code=code, identity=identity, daily_budget_usd=daily_budget_usd, now=now
        )
    except InviteError as exc:
        await session.rollback()
        return StartResult(StartOutcome.invite_invalid, failure=exc.reason)
    await session.commit()
    return StartResult(StartOutcome.invited, user=user)
