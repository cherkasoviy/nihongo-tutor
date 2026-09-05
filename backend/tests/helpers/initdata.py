"""Build Telegram Mini App ``initData`` strings the way the Telegram client does."""

from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import urlencode


def sign_init_data(bot_token: str, fields: dict[str, str]) -> str:
    """Return ``initData`` (query-string) with a valid ``hash`` for ``bot_token``."""
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def make_init_data(
    bot_token: str,
    *,
    tg_user_id: int,
    auth_date: int,
    first_name: str = "Аня",
    username: str | None = "anya",
    language_code: str = "ru",
    query_id: str = "AAHdF6IQAAAAAN0XohDhrOrc",
) -> str:
    user = {"id": tg_user_id, "first_name": first_name, "language_code": language_code, "allows_write_to_pm": True}
    if username:
        user["username"] = username
    fields = {
        "query_id": query_id,
        "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")),
        "auth_date": str(auth_date),
    }
    return sign_init_data(bot_token, fields)
