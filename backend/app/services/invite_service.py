"""Invite-only onboarding.

``redeem`` runs inside one transaction and locks the invite row with ``SELECT ... FOR UPDATE`` so
two learners racing for the last use of a ``max_uses=1`` code cannot both get in.
"""

from __future__ import annotations

import datetime as dt
import enum
import secrets
import string
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Invite, InviteRedemption, User, UserRole
from app.logging import get_logger
from app.services import user_service
from app.services.user_service import TelegramIdentity

log = get_logger(__name__)

_CODE_ALPHABET = string.ascii_uppercase.replace("O", "").replace("I", "") + string.digits.replace("0", "").replace(
    "1", ""
)
CODE_LENGTH = 8


class InviteFailure(enum.StrEnum):
    not_found = "not_found"
    revoked = "revoked"
    expired = "expired"
    exhausted = "exhausted"


class InviteError(Exception):
    def __init__(self, reason: InviteFailure) -> None:
        super().__init__(reason.value)
        self.reason = reason


def generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(raw: str) -> str:
    return raw.strip().upper()


async def create_invite(
    session: AsyncSession,
    *,
    created_by: User | None,
    max_uses: int = 1,
    ttl: dt.timedelta | None = dt.timedelta(days=14),
    note: str | None = None,
    now: dt.datetime | None = None,
) -> Invite:
    now = now or dt.datetime.now(dt.UTC)
    invite = Invite(
        code=generate_code(),
        created_by=created_by.id if created_by else None,
        max_uses=max(1, max_uses),
        expires_at=(now + ttl) if ttl else None,
        note=note,
    )
    session.add(invite)
    await session.flush()
    return invite


async def list_invites(session: AsyncSession, *, limit: int = 50) -> list[Invite]:
    result = await session.scalars(select(Invite).order_by(Invite.created_at.desc()).limit(limit))
    return list(result)


async def revoke_invite(session: AsyncSession, invite_id: uuid.UUID) -> Invite | None:
    invite = await session.get(Invite, invite_id)
    if invite is not None:
        invite.revoked = True
        await session.flush()
    return invite


async def redeem(
    session: AsyncSession,
    *,
    code: str,
    identity: TelegramIdentity,
    daily_budget_usd: float,
    now: dt.datetime | None = None,
) -> User:
    """Create the learner for ``identity`` by consuming one use of ``code``.

    Caller owns the transaction: on success the returned ``User`` is flushed but not committed.
    Raises ``InviteError`` without side effects otherwise.
    """
    now = now or dt.datetime.now(dt.UTC)
    invite = await session.scalar(select(Invite).where(Invite.code == normalize_code(code)).with_for_update())
    if invite is None:
        raise InviteError(InviteFailure.not_found)
    if invite.revoked:
        raise InviteError(InviteFailure.revoked)
    if invite.expires_at is not None and invite.expires_at <= now:
        raise InviteError(InviteFailure.expired)
    if invite.uses >= invite.max_uses:
        raise InviteError(InviteFailure.exhausted)

    inviter = await session.get(User, invite.created_by) if invite.created_by else None
    user = await user_service.create_user(
        session,
        identity,
        role=UserRole.learner,
        invited_by=inviter,
        daily_budget_usd=daily_budget_usd,
        now=now,
    )
    invite.uses += 1
    session.add(InviteRedemption(invite_id=invite.id, user_id=user.id, redeemed_at=now))
    await session.flush()
    log.info("invite redeemed", code=invite.code, tg_user_id=identity.tg_user_id, uses=invite.uses)
    return user
