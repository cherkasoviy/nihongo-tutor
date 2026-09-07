from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Any

from sqlalchemy import BigInteger, Boolean, Enum, ForeignKey, Integer, Numeric, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserRole(enum.StrEnum):
    learner = "learner"
    admin = "admin"


class UserStatus(enum.StrEnum):
    active = "active"
    paused = "paused"
    blocked = "blocked"


# What a learner gets before anyone tells us otherwise. The Mini App knows the browser's real zone
# and reports it on login; the backend adopts it only while this default is still in place, so a
# deliberate choice in Settings is never silently overwritten on the next login.
DEFAULT_TIMEZONE = "Europe/Moscow"


class FuriganaMode(enum.StrEnum):
    always = "always"
    auto = "auto"
    off = "off"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    tg_username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    language_code: Mapped[str | None] = mapped_column(String(16))

    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"), default=UserRole.learner, nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="user_status"), default=UserStatus.active, nullable=False
    )

    timezone: Mapped[str] = mapped_column(String(64), default=DEFAULT_TIMEZONE, nullable=False)
    reminder_time: Mapped[dt.time | None] = mapped_column(Time)
    daily_minutes_target: Mapped[int] = mapped_column(Integer, default=17, nullable=False)
    # The learner's chosen pace, or NULL to take the stage default. Only a starting number: the
    # planner's backlog and retention rules still apply on top, so a high setting cannot outrun the
    # review queue — it just stops the app being slower than the learner.
    daily_new_items_target: Mapped[int | None] = mapped_column(Integer)
    furigana_mode: Mapped[FuriganaMode] = mapped_column(
        Enum(FuriganaMode, name="furigana_mode"), default=FuriganaMode.auto, nullable=False
    )
    daily_budget_usd: Mapped[float] = mapped_column(Numeric(8, 4), default=0.35, nullable=False)
    desired_retention: Mapped[float] = mapped_column(Numeric(4, 3), default=0.90, nullable=False)
    fsrs_params: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)

    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    onboarded_at: Mapped[dt.datetime | None]
    last_active_at: Mapped[dt.datetime | None]

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.admin


class Invite(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "invites"

    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    max_uses: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    uses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[dt.datetime | None]
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str | None] = mapped_column(String(255))

    redemptions: Mapped[list[InviteRedemption]] = relationship(back_populates="invite")

    def is_usable(self, now: dt.datetime) -> bool:
        if self.revoked or self.uses >= self.max_uses:
            return False
        return self.expires_at is None or self.expires_at > now


class InviteRedemption(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "invite_redemptions"
    __table_args__ = (UniqueConstraint("invite_id", "user_id"),)

    invite_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invites.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    redeemed_at: Mapped[dt.datetime] = mapped_column(nullable=False)

    invite: Mapped[Invite] = relationship(back_populates="redemptions")
