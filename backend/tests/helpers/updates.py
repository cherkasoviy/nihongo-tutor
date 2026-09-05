"""Factories for Telegram ``Update`` objects."""

from __future__ import annotations

import datetime as dt
from itertools import count

from aiogram.types import Chat, Message, MessageEntity, Update, User

_ids = count(1000)


def make_user(tg_user_id: int, first_name: str = "Аня", username: str | None = "anya") -> User:
    return User(id=tg_user_id, is_bot=False, first_name=first_name, username=username, language_code="ru")


def command_update(tg_user_id: int, text: str, *, first_name: str = "Аня", username: str | None = "anya") -> Update:
    user = make_user(tg_user_id, first_name=first_name, username=username)
    command_len = len(text.split(" ", 1)[0])
    message = Message(
        message_id=next(_ids),
        date=dt.datetime.now(dt.UTC),
        chat=Chat(id=tg_user_id, type="private", first_name=first_name),
        from_user=user,
        text=text,
        entities=[MessageEntity(type="bot_command", offset=0, length=command_len)],
    )
    return Update(update_id=next(_ids), message=message)
