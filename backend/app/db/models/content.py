"""Shared content: the polymorphic ``items`` hub plus the concrete tables it points at.

Phase 1 added ``kana``; Phase 2 adds ``vocab_lemmas``. Grammar, sentences and dialogues follow.
Every concrete row is reachable from ``items`` through ``(type, ref_id)`` so a learner card needs
exactly one foreign key.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
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


class KanaScript(enum.StrEnum):
    hiragana = "hiragana"
    katakana = "katakana"


class KanaKind(enum.StrEnum):
    """Gojūon rows, their voiced variants, and the contracted (yōon) syllables."""

    basic = "basic"
    dakuten = "dakuten"
    handakuten = "handakuten"
    yoon = "yoon"


class Kana(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One syllable of one script.

    ``romaji_key`` never reaches the learner: it exists so a typed answer can be normalised
    (the UI shows Cyrillic Polivanov, the plan bans rendered romaji everywhere).
    """

    __tablename__ = "kana"
    __table_args__ = (UniqueConstraint("script", "char"),)

    char: Mapped[str] = mapped_column(String(4), nullable=False)
    script: Mapped[KanaScript] = mapped_column(Enum(KanaScript, name="kana_script"), nullable=False)
    romaji_key: Mapped[str] = mapped_column(String(8), nullable=False)
    cyrillic: Mapped[str] = mapped_column(String(8), nullable=False)
    row: Mapped[str] = mapped_column(String(8), nullable=False)
    kind: Mapped[KanaKind] = mapped_column(Enum(KanaKind, name="kana_kind"), nullable=False)
    group_order: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    mnemonic_ru: Mapped[str | None] = mapped_column(String(512))
    example_word: Mapped[str | None] = mapped_column(String(32))
    example_reading: Mapped[str | None] = mapped_column(String(32))
    example_gloss_ru: Mapped[str | None] = mapped_column(String(128))
    audio_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("audio_assets.id", ondelete="SET NULL"))


class Vocab(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One vocabulary entry. ``vocab_lemmas`` in the plan's data model.

    ``word`` is what the plan calls ``lemma``; the seed file authored it as ``word`` and the column
    follows the seed, because the seed is the source of truth and a rename between the two would be
    one more thing to remember in the importer.

    Two deliberate choices about types:

    - ``pos`` and ``gloss_review_status`` are ``String``, not ``Enum``. Content grows and a database
      enum needs a migration every time it does — tranche 2 alone added ``adverb``, ``numeral`` and
      ``determiner``. The permitted values are enforced in ``vocab_seed.py``, where a bad value
      fails ``nihongo-content check`` before it can reach a database.
    - ``jlpt_level``, ``freq_rank``, ``pitch_pattern``, ``jmdict_seq`` and ``kanji_forms`` exist and
      stay empty. ``docs/CONTENT.md`` explains why: this content is hand-authored, and the
      JMdict/Warodai/BCCWJ pipeline merges onto these rows later by ``slug`` rather than replacing
      them. The columns are here so that merge is an UPDATE and not a migration.
    """

    __tablename__ = "vocab_lemmas"

    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    word: Mapped[str] = mapped_column(String(64), nullable=False)
    reading: Mapped[str] = mapped_column(String(64), nullable=False)
    gloss_ru: Mapped[str] = mapped_column(String(512), nullable=False)
    pos: Mapped[str] = mapped_column(String(32), nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False, default=list)

    example_ja: Mapped[str | None] = mapped_column(String(256))
    example_reading: Mapped[str | None] = mapped_column(String(256))
    example_gloss_ru: Mapped[str | None] = mapped_column(String(512))

    gloss_source: Mapped[str] = mapped_column(String(16), nullable=False)
    gloss_review_status: Mapped[str] = mapped_column(String(16), nullable=False)

    # Filled by the enrichment pipeline, not by hand. See the class docstring.
    jlpt_level: Mapped[int | None] = mapped_column(Integer)
    freq_rank: Mapped[int | None] = mapped_column(Integer)
    pitch_pattern: Mapped[str | None] = mapped_column(String(32))
    jmdict_seq: Mapped[int | None] = mapped_column(Integer)
    kanji_forms: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))

    audio_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("audio_assets.id", ondelete="SET NULL"))
