"""``/help``.

Phase 1 implements every command this module used to stub out, so the placeholder is gone; the
learning commands live in ``handlers/session.py`` and ``handlers/progress.py``.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot import texts_ru

router = Router(name="help")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts_ru.HELP)
