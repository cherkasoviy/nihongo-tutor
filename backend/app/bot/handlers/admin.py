"""``/admin`` subcommands. Phase 0: invite management."""

from __future__ import annotations

import datetime as dt

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.utils.deep_linking import create_start_link
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import texts_ru
from app.config import Settings
from app.db.models import Invite
from app.services import invite_service, user_service

router = Router(name="admin")


def _fmt_expiry(invite: Invite) -> str:
    return invite.expires_at.strftime("%d.%m.%Y") if invite.expires_at else texts_ru.ADMIN_NO_EXPIRY


@router.message(Command("admin"))
async def cmd_admin(
    message: Message,
    command: CommandObject,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    if message.from_user is None:
        return
    async with sessionmaker() as session:
        user = await user_service.get_by_tg_id(session, message.from_user.id)
        if user is None:
            await message.answer(texts_ru.NOT_REGISTERED)
            return
        if not user.is_admin:
            await message.answer(texts_ru.ADMIN_ONLY)
            return

        args = (command.args or "").split()
        sub = args[0].lower() if args else ""

        if sub == "invite":
            try:
                max_uses = int(args[1]) if len(args) > 1 else 1
                days = int(args[2]) if len(args) > 2 else 14
            except ValueError:
                await message.answer(texts_ru.ADMIN_INVITE_USAGE)
                return
            invite = await invite_service.create_invite(
                session,
                created_by=user,
                max_uses=max_uses,
                ttl=dt.timedelta(days=days) if days > 0 else None,
            )
            await session.commit()
            link = await create_start_link(message.bot, invite.code) if message.bot else invite.code
            await message.answer(
                texts_ru.ADMIN_INVITE_CREATED.format(
                    code=invite.code, link=link, max_uses=invite.max_uses, expires=_fmt_expiry(invite)
                )
            )
            return

        if sub == "invites":
            invites = await invite_service.list_invites(session, limit=20)
            if not invites:
                await message.answer(texts_ru.ADMIN_INVITES_EMPTY)
                return
            rows = [
                texts_ru.ADMIN_INVITE_ROW.format(
                    code=i.code,
                    uses=i.uses,
                    max_uses=i.max_uses,
                    revoked=texts_ru.ADMIN_INVITE_REVOKED_MARK if i.revoked else "",
                    expires=_fmt_expiry(i),
                )
                for i in invites
            ]
            await message.answer("\n".join([texts_ru.ADMIN_INVITES_HEADER, *rows]))
            return

        await message.answer(texts_ru.ADMIN_HELP)
