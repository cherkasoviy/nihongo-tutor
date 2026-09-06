"""Pydantic response/request models shared by API routers (source of the Mini App's generated types)."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Invite, User


class TelegramAuthRequest(BaseModel):
    init_data: str = Field(min_length=1, description="Raw window.Telegram.WebApp.initData string")
    timezone: str | None = Field(
        default=None,
        max_length=64,
        description="IANA zone the browser reports; adopted only while the learner is on the default",
    )


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


# --- Phase 1: kana, session, stats, settings ---------------------------------


class KanaCellOut(BaseModel):
    """One cell of the Mini App's kana grid."""

    item_id: uuid.UUID
    char: str
    script: str
    cyrillic: str
    row: str
    kind: str
    group_order: int
    mnemonic_ru: str | None
    example_word: str | None
    example_reading: str | None
    example_gloss_ru: str | None
    introduced: bool
    state: str | None
    retrievability: float | None
    due: dt.datetime | None
    reps: int


class SessionStepOut(BaseModel):
    id: uuid.UUID
    idx: int
    kind: str
    status: str
    prompt: str | None = None
    char: str | None = None
    cyrillic: str | None = None
    mnemonic_ru: str | None = None
    example_word: str | None = None
    example_gloss_ru: str | None = None
    choices: list[str] = Field(default_factory=list)
    mode: str = "choice"


class SessionOut(BaseModel):
    id: uuid.UUID
    local_date: dt.date
    kind: str = "daily"
    planned_steps: int
    completed_steps: int
    outcome: str
    current: SessionStepOut | None = None


class AnswerIn(BaseModel):
    choice: int | None = Field(default=None, ge=0, le=15)
    self_grade: str | None = Field(default=None, pattern="^(forgot|knew|easy)$")
    acknowledged: bool = False


class AnswerOut(BaseModel):
    accepted: bool
    correct: bool
    correct_label: str
    rating: int | None
    session_finished: bool
    next: SessionStepOut | None = None


class StatsOut(BaseModel):
    kana_total: int
    kana_introduced: int
    kana_known: int
    due_now: int
    reviews_7d: int
    retention_7d: float | None
    streak_current: int
    streak_longest: int
    freezes_available: int
    sessions_completed: int
    minutes_7d: float


class SettingsIn(BaseModel):
    timezone: str | None = Field(default=None, max_length=64)
    reminder_time: dt.time | None = None
    clear_reminder: bool = False
    daily_minutes_target: int | None = Field(default=None, ge=5, le=60)
    furigana_mode: str | None = Field(default=None, pattern="^(always|auto|off)$")
