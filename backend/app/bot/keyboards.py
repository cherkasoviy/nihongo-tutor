from __future__ import annotations

import uuid
from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.bot import texts_ru
from app.bot.callbacks import (
    SessionAction,
    SetPace,
    SetReminder,
    StepAck,
    StepChoice,
    StepReveal,
    StepSelfGrade,
)


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


# Evening-weighted: the plan's session is 15-20 minutes of focused recall, which most people do
# after work rather than before it. Every option is the learner's own local time.
REMINDER_HOURS = (8, 12, 18, 20, 21, 22)


def reminder_keyboard(*, include_off: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=f"{h:02d}:00", callback_data=SetReminder(hour=h).pack())
            for h in REMINDER_HOURS[i : i + 3]
        ]
        for i in range(0, len(REMINDER_HOURS), 3)
    ]
    if include_off:
        rows.append([InlineKeyboardButton(text=texts_ru.BUTTON_NO_REMINDER, callback_data=SetReminder(hour=-1).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Spanning the range the simulation covered: the default, a gentler pace, and the faster end that
# still fits the time budget. Anything above MAX_NEW_PER_DAY is refused upstream.
PACE_CHOICES = (5, 8, 10, 12, 15, 20)


def pace_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=str(n), callback_data=SetPace(count=n).pack()) for n in PACE_CHOICES[i : i + 3]]
        for i in range(0, len(PACE_CHOICES), 3)
    ]
    rows.append([InlineKeyboardButton(text=texts_ru.BUTTON_PACE_DEFAULT, callback_data=SetPace(count=0).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)
