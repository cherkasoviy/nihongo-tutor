"""Pydantic response/request models shared by API routers (source of the Mini App's generated types)."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Invite, User


class TelegramAuthRequest(BaseModel):
    init_data: str = Field(min_length=1, description="Raw window.Telegram.WebApp.initData string")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tg_user_id: int
    first_name: str | None
    tg_username: str | None
    role: str
    status: str
    timezone: str
    reminder_time: dt.time | None
    daily_minutes_target: int
    furigana_mode: str
    onboarded_at: dt.datetime | None

    @classmethod
    def from_user(cls, user: User) -> UserOut:
        return cls.model_validate(user)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 (OAuth scheme name, not a secret)
    expires_in: int
    user: UserOut


class InviteCreate(BaseModel):
    max_uses: int = Field(default=1, ge=1, le=100)
    ttl_days: int | None = Field(default=14, ge=1, le=365, description="None = no expiry")
    note: str | None = Field(default=None, max_length=255)


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    max_uses: int
    uses: int
    expires_at: dt.datetime | None
    revoked: bool
    note: str | None
    created_at: dt.datetime
    start_link: str | None = None

    @classmethod
    def from_invite(cls, invite: Invite, bot_username: str | None) -> InviteOut:
        out = cls.model_validate(invite)
        if bot_username:
            out.start_link = f"https://t.me/{bot_username}?start={invite.code}"
        return out


class HealthOut(BaseModel):
    status: str
    env: str
    version: str
