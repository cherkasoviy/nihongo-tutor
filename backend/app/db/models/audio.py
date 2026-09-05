from __future__ import annotations

from typing import Any

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AudioAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Cached TTS output. ``hash`` = sha256(provider|voice|rate|ssml_version|text)."""

    __tablename__ = "audio_assets"

    hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    voice: Mapped[str] = mapped_column(String(64), nullable=False)
    rate: Mapped[float] = mapped_column(nullable=False)
    text: Mapped[str] = mapped_column(String(2048), nullable=False)
    ogg_path: Mapped[str | None] = mapped_column(String(255))
    mp3_path: Mapped[str | None] = mapped_column(String(255))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    timepoints: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    tg_file_id: Mapped[str | None] = mapped_column(String(255))
