from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    UniqueConstraint,
)
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
