"""Shared content. Phase 0 ships only the polymorphic ``items`` hub; kana/vocab/grammar/sentence
tables arrive with their phases and reference ``items`` through ``ref_id``."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ItemType(enum.StrEnum):
    kana = "kana"
    vocab = "vocab"
    grammar = "grammar"
    sentence = "sentence"
    dialogue = "dialogue"


class ItemStage(enum.StrEnum):
    kana_hira = "kana_hira"
    kana_kata = "kana_kata"
    core = "core"


class Item(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("type", "ref_id"),)

    type: Mapped[ItemType] = mapped_column(Enum(ItemType, name="item_type"), nullable=False)
    ref_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    curriculum_order: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    stage: Mapped[ItemStage] = mapped_column(Enum(ItemStage, name="item_stage"), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
