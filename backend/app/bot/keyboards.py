from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.bot import texts_ru


def open_app_keyboard(miniapp_url: str, start_param: str | None = None) -> InlineKeyboardMarkup | None:
    """Button that opens the Mini App. Telegram only accepts https URLs for web_app buttons."""
    if not miniapp_url.startswith("https://"):
        return None
    url = miniapp_url if not start_param else f"{miniapp_url}?startapp={start_param}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts_ru.OPEN_APP_BUTTON, web_app=WebAppInfo(url=url))]]
    )
