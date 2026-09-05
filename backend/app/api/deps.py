"""FastAPI dependencies: settings, current user from the Bearer JWT, admin guard."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.security import TokenError, decode_access_token
from app.config import Settings, get_settings
from app.db.base import SessionDep
from app.db.models import User, UserStatus

_bearer = HTTPBearer(auto_error=False)

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    try:
        claims = decode_access_token(credentials.credentials, secret=settings.jwt_secret.get_secret_value())
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc
    user = await session.get(User, claims.user_id)
    if user is None or user.tg_user_id != claims.tg_user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="unknown user")
    if user.status == UserStatus.blocked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="blocked")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="admin only")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
