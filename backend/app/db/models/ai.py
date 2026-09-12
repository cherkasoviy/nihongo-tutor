from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AiUsageLedger(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per paid provider call. ``user_id`` NULL means shared content generation."""

    __tablename__ = "ai_usage_ledger"
    __table_args__ = (Index("ix_ai_usage_ledger_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # google (Vertex, TTS, STT) | anthropic
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    audio_seconds: Mapped[float | None] = mapped_column(Numeric(10, 3))
    chars: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128))
    session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(32))
