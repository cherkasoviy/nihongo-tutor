from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CardDirection(enum.StrEnum):
    recognition = "recognition"
    production = "production"
    listening = "listening"


class CardState(enum.StrEnum):
    """Mirrors ``fsrs.State`` so the DB does not depend on the library's int values."""

    new = "new"
    learning = "learning"
    review = "review"
    relearning = "relearning"


class Card(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cards"
    __table_args__ = (
        UniqueConstraint("user_id", "item_id", "direction"),
        Index("ix_cards_user_due", "user_id", "due"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    direction: Mapped[CardDirection] = mapped_column(Enum(CardDirection, name="card_direction"), nullable=False)

    state: Mapped[CardState] = mapped_column(Enum(CardState, name="card_state"), default=CardState.new, nullable=False)
    # Index into the scheduler's learning_steps/relearning_steps; None once the card reaches review.
    step: Mapped[int | None] = mapped_column(Integer)
    stability: Mapped[float | None] = mapped_column(Float)
    difficulty: Mapped[float | None] = mapped_column(Float)
    due: Mapped[dt.datetime] = mapped_column(nullable=False)
    last_review: Mapped[dt.datetime | None]
    reps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lapses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    elapsed_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    scheduled_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ReviewLog(UUIDPrimaryKeyMixin, Base):
    """Append-only; the FSRS optimizer reads it (excluding ``intra_session`` rows)."""

    __tablename__ = "review_logs"
    __table_args__ = (
        Index("ix_review_logs_card_review_at", "card_id", "review_at"),
        CheckConstraint("rating BETWEEN 1 AND 4", name="rating_range"),
    )

    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), nullable=False)
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1 Again, 2 Hard, 3 Good, 4 Easy
    review_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    elapsed_days: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_days: Mapped[int] = mapped_column(Integer, nullable=False)
    state_before: Mapped[CardState] = mapped_column(
        Enum(CardState, name="card_state", create_type=False), nullable=False
    )
    response_ms: Mapped[int | None] = mapped_column(Integer)
    auto_graded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    intra_session: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    answer_payload: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)


class SessionClient(enum.StrEnum):
    bot = "bot"
    miniapp = "miniapp"


class SessionOutcome(enum.StrEnum):
    in_progress = "in_progress"
    completed = "completed"
    abandoned = "abandoned"


class SessionKind(enum.StrEnum):
    """Why this session exists.

    ``daily`` is the planned lesson: it carries the day's new items and it is what earns the streak.
    ``practice`` is extra work the learner asked for after finishing — reviews only, never new items,
    and it cannot earn the day a second time. Keeping them apart is what lets a keen learner do more
    without the schedule, the streak or the new-item pacing quietly drifting.
    """

    daily = "daily"
    practice = "practice"


class StepKind(enum.StrEnum):
    """The step vocabulary of the session engine. Phase 1 emits the kana-stage subset;
    cloze/listen_choose/speak/roleplay are reserved for Phases 2-3."""

    review_recog = "review_recog"
    review_prod = "review_prod"
    intro_item = "intro_item"
    cloze = "cloze"
    listen_choose = "listen_choose"
    shadow = "shadow"
    speak = "speak"
    roleplay = "roleplay"
    wrapup = "wrapup"


class StepStatus(enum.StrEnum):
    pending = "pending"
    shown = "shown"
    answered = "answered"
    skipped = "skipped"


class LearningSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One sitting. ``local_date`` is the learner's own date, so a session is never split by UTC
    midnight and "today" means the same thing in the bot and the Mini App.

    A date holds at most one ``daily`` session and any number of ``practice`` ones.
    """

    __tablename__ = "learning_sessions"
    __table_args__ = (
        Index("ix_learning_sessions_user_date", "user_id", "local_date"),
        # "At most one planned lesson per learner-day", enforced rather than merely intended.
        # The day's new-item dose is a deliberate decision; a second daily session would issue it
        # twice. A bug in the stop handler did exactly that, and only the absence of this index
        # let it through — practice sittings are unlimited by design and stay outside the index.
        Index(
            "uq_learning_sessions_daily_per_day",
            "user_id",
            "local_date",
            unique=True,
            postgresql_where=text("kind = 'daily'"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    local_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    finished_at: Mapped[dt.datetime | None]
    client: Mapped[SessionClient] = mapped_column(
        Enum(SessionClient, name="session_client"), default=SessionClient.bot, nullable=False
    )
    kind: Mapped[SessionKind] = mapped_column(
        Enum(SessionKind, name="session_kind"), default=SessionKind.daily, nullable=False
    )
    planned_steps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_steps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    outcome: Mapped[SessionOutcome] = mapped_column(
        Enum(SessionOutcome, name="session_outcome"), default=SessionOutcome.in_progress, nullable=False
    )
    active_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class SessionStep(UUIDPrimaryKeyMixin, Base):
    """A materialised step. ``status`` is what makes a callback idempotent: answering a step that
    is no longer ``pending``/``shown`` is a no-op, so a double tap cannot grade twice."""

    __tablename__ = "session_steps"
    __table_args__ = (
        UniqueConstraint("session_id", "idx"),
        Index("ix_session_steps_session_status", "session_id", "status"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("learning_sessions.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[StepKind] = mapped_column(Enum(StepKind, name="step_kind"), nullable=False)
    card_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"))
    item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    status: Mapped[StepStatus] = mapped_column(
        Enum(StepStatus, name="step_status"), default=StepStatus.pending, nullable=False
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    shown_at: Mapped[dt.datetime | None]
    answered_at: Mapped[dt.datetime | None]
    tg_message_id: Mapped[int | None] = mapped_column(Integer)


class DailyPlan(Base):
    """The blueprint the planner produced for one learner-day; steps are materialised lazily."""

    __tablename__ = "daily_plans"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    local_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    new_items_target: Mapped[int] = mapped_column(Integer, nullable=False)
    due_count: Mapped[int] = mapped_column(Integer, nullable=False)
    retention_7d: Mapped[float | None] = mapped_column(Float)
    backlog_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    plan: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), nullable=False)


class Streak(Base):
    """Forgiving streak: one automatic freeze per week covers a single missed day.

    Dates are the learner's local dates, so a streak never breaks because of a timezone or DST shift.
    """

    __tablename__ = "streaks"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    longest: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_active_date: Mapped[dt.date | None] = mapped_column(Date)
    freezes_available: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    freeze_earned_week: Mapped[str | None] = mapped_column(String(16))
    freeze_used_dates: Mapped[list[dt.date]] = mapped_column(ARRAY(Date), default=list, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)
