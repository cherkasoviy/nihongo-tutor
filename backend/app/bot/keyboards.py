from __future__ import annotations

import uuid
from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.bot import texts_ru
from app.bot.callbacks import SessionAction, StepAck, StepChoice, StepReveal, StepSelfGrade


def open_app_keyboard(miniapp_url: str, start_param: str | None = None) -> InlineKeyboardMarkup | None:
    """Button that opens the Mini App. Telegram only accepts https URLs for web_app buttons."""
    if not miniapp_url.startswith("https://"):
        return None
    url = miniapp_url if not start_param else f"{miniapp_url}?startapp={start_param}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts_ru.OPEN_APP_BUTTON, web_app=WebAppInfo(url=url))]]
    )


def choice_keyboard(step_id: uuid.UUID, labels: Sequence[str], *, columns: int = 2) -> InlineKeyboardMarkup:
    """A grid of answer options.

    Two columns by default: Telegram renders wide buttons poorly on narrow phones, and a 2x2 grid of
    four options is one glance rather than a list to read top to bottom.
    """
    buttons = [
        InlineKeyboardButton(text=label, callback_data=StepChoice(step_id=step_id, choice=i).pack())
        for i, label in enumerate(labels)
    ]
    rows = [buttons[i : i + columns] for i in range(0, len(buttons), columns)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reveal_keyboard(step_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts_ru.BUTTON_REVEAL, callback_data=StepReveal(step_id=step_id).pack())]
        ]
    )


def self_grade_keyboard(step_id: uuid.UUID) -> InlineKeyboardMarkup:
    """Не помню / Помню / Легко — the plan's three-button self grading."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label, callback_data=StepSelfGrade(step_id=step_id, grade=grade).pack())
                for grade, label in (
                    ("forgot", texts_ru.BUTTON_FORGOT),
                    ("knew", texts_ru.BUTTON_KNEW),
                    ("easy", texts_ru.BUTTON_EASY),
                )
            ]
        ]
    )


def ack_keyboard(step_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts_ru.BUTTON_NEXT, callback_data=StepAck(step_id=step_id).pack())]
        ]
    )


def stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts_ru.BUTTON_STOP, callback_data=SessionAction(action="stop").pack())]
        ]
    )
