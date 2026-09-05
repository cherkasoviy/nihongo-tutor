"""``/help`` and placeholders for commands that arrive in later phases."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot import texts_ru

router = Router(name="help")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts_ru.HELP)


@router.message(Command("today", "review", "stats", "settings", "pause", "resume"))
async def cmd_coming_soon(message: Message) -> None:
    await message.answer(texts_ru.COMING_SOON)
