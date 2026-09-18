"""Load and validate the hand-authored vocabulary seed.

The seed lives in ``backend/data/seed/vocab_core.json`` and is the source of truth for
``vocab_lemmas``: the database is disposable, this file is not. Validation mirrors
``kana_seed.py`` and is strict for the same reason — a learner cannot detect a subtly wrong
Japanese sentence, so they will simply learn it.

What the strictness buys, concretely: the headword-in-example rule below found that entry 90
carried ``word: "だれ"`` while its own example sentence wrote 誰. Nothing else in the pipeline
would have noticed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SEED_DIR: Final = Path(__file__).resolve().parents[2] / "data" / "seed"
SEED_FILE: Final = "vocab_core.json"

# Deliberately the exact set the content uses, not a generous superset. A speculative ``particle``
# nobody has authored yet would only make a typo like "adjectiv" harder to catch; widening this is
# a one-line change made on purpose when a tranche needs it.
POS_VALUES: Final = frozenset({"noun", "verb", "adjective", "adverb", "pronoun", "determiner", "numeral", "expression"})
# docs/CONTENT.md rule 3.
GLOSS_SOURCES: Final = frozenset({"jmdict_rus", "warodai", "claude", "human"})
REVIEW_STATUSES: Final = frozenset({"needs_review", "approved", "rejected"})
APPROVED: Final = "approved"

_LATIN: Final = re.compile(r"[A-Za-z]")
# Hiragana, katakana, the long-vowel mark and the iteration mark. Kanji is excluded: a reading is
# what furigana and the synthesiser consume, and both need it unambiguous.
_KANA: Final = re.compile(r"^[ぁ-ゟ゠-ヿー々・]+$")
# A sentence reading is the same plus the punctuation the sentences actually carry.
_KANA_SENTENCE: Final = re.compile(r"^[ぁ-ゟ゠-ヿー々・、。！？]+$")


def _reject_latin(value: str | None) -> str | None:
    """No romaji reaches the learner — the plan bans it everywhere in the UI."""
    if value is not None and _LATIN.search(value):
        raise ValueError(f"latin letters in a learner-facing field: {value!r}")
    return value


def example_stems(word: str) -> set[str]:
    """Forms of ``word`` that may legitimately stand in for it inside an example sentence.

    A conjugated verb or adjective never appears in its dictionary form, so requiring a literal
    match would reject almost every verb in the file. Dropping the final character covers the
    inflecting classes — 食べる→食べ(ます), 行く→行(きます), 高い→高(いです) — and a する compound
    also matches on its noun alone, since 勉強する appears as 勉強します.

    This is deliberately blunt. It is a check that the example demonstrates the headword at all,
    not a conjugation engine; Sudachi is the right tool the day the pipeline needs one.
    """
    forms = {word}
    if len(word) > 1:
        forms.add(word[:-1])
    if word.endswith("する") and len(word) > 2:
        forms.add(word[:-2])
    return {f for f in forms if f}


class VocabExample(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ja: str = Field(min_length=1, max_length=256)
    reading: str = Field(min_length=1, max_length=256)
    gloss_ru: str = Field(min_length=1, max_length=512)

    @field_validator("ja", "reading", "gloss_ru")
    @classmethod
    def _no_latin(cls, value: str) -> str:
        return _reject_latin(value) or value

    @field_validator("reading")
    @classmethod
    def _kana_only(cls, value: str) -> str:
        if not _KANA_SENTENCE.match(value):
            raise ValueError(f"example reading must be kana and punctuation only, got {value!r}")
        return value


class VocabSeedEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str = Field(min_length=1, max_length=128, pattern=r"^vocab:[a-z0-9-]+$")
    word: str = Field(min_length=1, max_length=64)
    reading: str = Field(min_length=1, max_length=64)
    gloss_ru: str = Field(min_length=1, max_length=512)
    pos: str = Field(min_length=1, max_length=32)
    curriculum_order: int = Field(ge=1)
    tags: tuple[str, ...] = ()
    example: VocabExample
    gloss_source: str
    gloss_review_status: str

    @field_validator("word", "reading", "gloss_ru")
    @classmethod
    def _no_latin(cls, value: str) -> str:
        return _reject_latin(value) or value

    @field_validator("reading")
    @classmethod
    def _kana_only(cls, value: str) -> str:
        if not _KANA.match(value):
            raise ValueError(f"reading must be kana only, got {value!r}")
        return value

    @field_validator("pos")
    @classmethod
    def _known_pos(cls, value: str) -> str:
        if value not in POS_VALUES:
            raise ValueError(f"unknown pos {value!r}; expected one of {sorted(POS_VALUES)}")
        return value

    @field_validator("gloss_source")
    @classmethod
    def _known_source(cls, value: str) -> str:
        if value not in GLOSS_SOURCES:
            raise ValueError(f"unknown gloss_source {value!r}; expected one of {sorted(GLOSS_SOURCES)}")
        return value

    @field_validator("gloss_review_status")
    @classmethod
    def _known_status(cls, value: str) -> str:
        if value not in REVIEW_STATUSES:
            raise ValueError(f"unknown gloss_review_status {value!r}; expected one of {sorted(REVIEW_STATUSES)}")
        return value

    @field_validator("tags")
    @classmethod
    def _tags_are_slugs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError(f"duplicate tag in {values!r}")
        for tag in values:
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", tag):
                raise ValueError(f"tag must be a lowercase slug, got {tag!r}")
        return values

    @model_validator(mode="after")
    def _example_demonstrates_the_headword(self) -> VocabSeedEntry:
        if not any(stem in self.example.ja for stem in example_stems(self.word)):
            raise ValueError(f"{self.slug}: example {self.example.ja!r} does not contain {self.word!r} or its stem")
        return self

    @property
    def is_approved(self) -> bool:
        return self.gloss_review_status == APPROVED


class VocabSeedFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    kind: str
    note: str | None = None
    vocab: tuple[VocabSeedEntry, ...]

    @field_validator("kind")
    @classmethod
    def _is_vocab(cls, value: str) -> str:
        if value != "vocab":
            raise ValueError(f"expected kind=vocab, got {value!r}")
        return value

    @field_validator("vocab")
    @classmethod
    def _consistent(cls, entries: tuple[VocabSeedEntry, ...]) -> tuple[VocabSeedEntry, ...]:
        if not entries:
            raise ValueError("empty seed file")
        slugs = [e.slug for e in entries]
        if len(set(slugs)) != len(slugs):
            dupes = sorted({s for s in slugs if slugs.count(s) > 1})
            raise ValueError(f"duplicate slug: {dupes}")
        orders = [e.curriculum_order for e in entries]
        if len(set(orders)) != len(orders):
            collisions = sorted({o for o in orders if orders.count(o) > 1})
            raise ValueError(f"duplicate curriculum_order: {collisions}")
        # Ascending, not merely unique: the file's reading order and the curriculum's order are the
        # same thing, and a diff that reorders entries silently is the failure this catches.
        if orders != sorted(orders):
            raise ValueError("curriculum_order must be ascending in file order")
        return entries


def load_vocab(*, seed_dir: Path | None = None) -> VocabSeedFile:
    path = (seed_dir or SEED_DIR) / SEED_FILE
    return VocabSeedFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
