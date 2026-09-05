"""``POST /api/auth/telegram``: initData -> JWT. ``GET /api/auth/me``: who am I."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, SettingsDep
from app.api.schemas import TelegramAuthRequest, TokenResponse, UserOut
from app.api.security import InitDataError, create_access_token, verify_init_data
from app.db.base import SessionDep
from app.services import user_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/telegram", response_model=TokenResponse)
async def auth_telegram(body: TelegramAuthRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    try:
        parsed = verify_init_data(
            body.init_data,
            bot_token=settings.bot_token.get_secret_value(),
            max_age_seconds=settings.initdata_max_age_seconds,
        )
    except InitDataError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    assert parsed.user is not None
    user = await user_service.get_by_tg_id(session, parsed.user.id)
    if user is None:
        # Registration happens only through the bot's invite flow.
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="not registered")

    now = dt.datetime.now(dt.UTC)
    user_service.apply_identity(
        user,
        user_service.TelegramIdentity(
            tg_user_id=parsed.user.id,
            username=parsed.user.username,
            first_name=parsed.user.first_name,
            language_code=parsed.user.language_code,
        ),
        now,
    )
    await session.commit()

    token = create_access_token(
        user_id=user.id,
        tg_user_id=user.tg_user_id,
        role=user.role.value,
        secret=settings.jwt_secret.get_secret_value(),
        ttl_seconds=settings.jwt_ttl_seconds,
        now=now,
    )
    return TokenResponse(access_token=token, expires_in=settings.jwt_ttl_seconds, user=UserOut.from_user(user))


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.from_user(user)
