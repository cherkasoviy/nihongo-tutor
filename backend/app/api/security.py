"""Telegram Mini App ``initData`` verification and short-lived JWT issuance.

The Mini App posts the raw ``initData`` string once; the backend verifies the HMAC with the bot
token (``aiogram.utils.web_app.safe_parse_webapp_init_data``), checks ``auth_date`` freshness and
answers with an HS256 JWT that every subsequent ``/api`` call carries as a Bearer token.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

import jwt
from aiogram.utils.web_app import WebAppInitData, safe_parse_webapp_init_data

JWT_ALGORITHM = "HS256"


class InitDataError(ValueError):
    """Raised when initData is missing, tampered with or too old."""


class TokenError(ValueError):
    """Raised when a JWT is missing, expired or malformed."""


def verify_init_data(
    init_data: str,
    *,
    bot_token: str,
    max_age_seconds: int,
    now: dt.datetime | None = None,
) -> WebAppInitData:
    if not init_data:
        raise InitDataError("initData is empty")
    if not bot_token:
        raise InitDataError("bot token is not configured")
    try:
        parsed = safe_parse_webapp_init_data(token=bot_token, init_data=init_data)
    except ValueError as exc:
        raise InitDataError("initData signature is invalid") from exc

    now = now or dt.datetime.now(dt.UTC)
    auth_date = parsed.auth_date
    if auth_date.tzinfo is None:
        auth_date = auth_date.replace(tzinfo=dt.UTC)
    age = (now - auth_date).total_seconds()
    if age > max_age_seconds:
        raise InitDataError("initData is too old")
    if age < -300:  # clock skew guard: auth_date in the far future is suspicious
        raise InitDataError("initData auth_date is in the future")
    if parsed.user is None:
        raise InitDataError("initData has no user")
    return parsed


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: uuid.UUID
    tg_user_id: int
    role: str
    expires_at: dt.datetime


def create_access_token(
    *,
    user_id: uuid.UUID,
    tg_user_id: int,
    role: str,
    secret: str,
    ttl_seconds: int,
    now: dt.datetime | None = None,
) -> str:
    if not secret:
        raise TokenError("JWT secret is not configured")
    now = now or dt.datetime.now(dt.UTC)
    payload = {
        "sub": str(user_id),
        "tg": tg_user_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(seconds=ttl_seconds)).timestamp()),
        "iss": "nihongo-tutor",
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, *, secret: str) -> TokenClaims:
    if not secret:
        raise TokenError("JWT secret is not configured")
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            issuer="nihongo-tutor",
            options={"require": ["sub", "tg", "role", "exp", "iat"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    try:
        return TokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            tg_user_id=int(payload["tg"]),
            role=str(payload["role"]),
            expires_at=dt.datetime.fromtimestamp(int(payload["exp"]), tz=dt.UTC),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenError("malformed token payload") from exc
