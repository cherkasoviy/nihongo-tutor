import asyncio
import datetime as dt

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Invite, InviteRedemption, User, UserRole
from app.services import invite_service, user_service
from app.services.invite_service import InviteError, InviteFailure
from app.services.user_service import TelegramIdentity
from tests.conftest import fresh_tg_id

pytestmark = pytest.mark.integration


async def _make_invite(sessionmaker: async_sessionmaker[AsyncSession], **kwargs: object) -> str:
    async with sessionmaker() as s:
        invite = await invite_service.create_invite(s, created_by=None, **kwargs)  # type: ignore[arg-type]
        await s.commit()
        return invite.code


async def _redeem(sessionmaker: async_sessionmaker[AsyncSession], code: str, tg_id: int) -> User | InviteError:
    async with sessionmaker() as s:
        try:
            user = await invite_service.redeem(
                s, code=code, identity=TelegramIdentity(tg_user_id=tg_id), daily_budget_usd=0.35
            )
        except InviteError as exc:
            await s.rollback()
            return exc
        await s.commit()
        return user


async def test_redeem_creates_learner_and_redemption(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    code = await _make_invite(sessionmaker, max_uses=1)
    result = await _redeem(sessionmaker, code.lower(), fresh_tg_id())
    assert isinstance(result, User)
    assert result.role == UserRole.learner

    async with sessionmaker() as s:
        invite = await s.scalar(select(Invite).where(Invite.code == code))
        assert invite is not None and invite.uses == 1
        assert await s.scalar(select(func.count()).select_from(InviteRedemption)) == 1


async def test_concurrent_redemption_of_single_use_invite(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """Two learners race for one seat: SELECT ... FOR UPDATE lets exactly one through."""
    code = await _make_invite(sessionmaker, max_uses=1)
    results = await asyncio.gather(*(_redeem(sessionmaker, code, fresh_tg_id()) for _ in range(6)))

    winners = [r for r in results if isinstance(r, User)]
    losers = [r for r in results if isinstance(r, InviteError)]
    assert len(winners) == 1
    assert len(losers) == 5
    assert {e.reason for e in losers} == {InviteFailure.exhausted}

    async with sessionmaker() as s:
        invite = await s.scalar(select(Invite).where(Invite.code == code))
        assert invite is not None and invite.uses == 1
        assert await s.scalar(select(func.count()).select_from(User)) == 1


async def test_multi_use_invite_admits_exactly_max_uses(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    code = await _make_invite(sessionmaker, max_uses=3)
    results = await asyncio.gather(*(_redeem(sessionmaker, code, fresh_tg_id()) for _ in range(8)))
    assert sum(isinstance(r, User) for r in results) == 3


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"ttl": dt.timedelta(seconds=-1)}, InviteFailure.expired),
        ({"revoked": True}, InviteFailure.revoked),
    ],
)
async def test_expired_and_revoked(
    sessionmaker: async_sessionmaker[AsyncSession], kwargs: dict[str, object], expected: InviteFailure
) -> None:
    revoked = bool(kwargs.pop("revoked", False))
    code = await _make_invite(sessionmaker, **kwargs)
    if revoked:
        async with sessionmaker() as s:
            invite = await s.scalar(select(Invite).where(Invite.code == code))
            assert invite is not None
            await invite_service.revoke_invite(s, invite.id)
            await s.commit()
    result = await _redeem(sessionmaker, code, fresh_tg_id())
    assert isinstance(result, InviteError) and result.reason == expected


async def test_unknown_code(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    result = await _redeem(sessionmaker, "NOPE1234", fresh_tg_id())
    assert isinstance(result, InviteError) and result.reason == InviteFailure.not_found


async def test_ensure_admins_promotes_existing_users(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    tg_id = fresh_tg_id()
    async with sessionmaker() as s:
        await user_service.create_user(
            s,
            TelegramIdentity(tg_user_id=tg_id),
            role=UserRole.learner,
            invited_by=None,
            daily_budget_usd=0.35,
            now=dt.datetime.now(dt.UTC),
        )
        await s.commit()
    async with sessionmaker() as s:
        assert await user_service.ensure_admins(s, [tg_id, 999]) == 1
        await s.commit()
        user = await user_service.get_by_tg_id(s, tg_id)
        assert user is not None and user.role == UserRole.admin
        assert await user_service.ensure_admins(s, [tg_id]) == 0
