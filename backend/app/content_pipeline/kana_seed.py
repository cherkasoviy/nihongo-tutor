"""Load and validate the hand-authored kana seed files.

The seed lives in ``backend/data/seed/kana_{hiragana,katakana}.json`` and is the only content the app
ships with in Phase 1. Validation is strict on purpose: a wrong ``group_order`` silently reorders the
whole curriculum, and a stray latin character would put romaji in front of a learner, which the plan
forbids outright.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.content import KanaKind, KanaScript

SEED_DIR: Final = Path(__file__).resolve().parents[2] / "data" / "seed"
SEED_FILES: Final[dict[KanaScript, str]] = {
    KanaScript.hiragana: "kana_hiragana.json",
    KanaScript.katakana: "kana_katakana.json",
}

_LATIN: Final = re.compile(r"[A-Za-z]")
# Kana, the long-vowel mark, the iteration mark and ASCII-safe punctuation. Kanji is out: a learner in
# the kana stage cannot read it yet.
_KANA_ONLY: Final = re.compile(r"^[぀-ゟ゠-ヿー々ー・]+$")


class KanaSeedEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    char: str = Field(min_length=1, max_length=4)
    romaji_key: str = Field(min_length=1, max_length=8, pattern=r"^[a-z]+$")
    cyrillic: str = Field(min_length=1, max_length=8)
    row: str = Field(min_length=1, max_length=8, pattern=r"^[a-z]+$")
    kind: KanaKind
    group_order: int = Field(ge=1)
    mnemonic_ru: str | None = Field(default=None, max_length=512)
    example_word: str | None = Field(default=None, max_length=32)
    example_reading: str | None = Field(default=None, max_length=32)
    example_gloss_ru: str | None = Field(default=None, max_length=128)

    @field_validator("cyrillic", "mnemonic_ru", "example_gloss_ru")
    @classmethod
    def _no_latin(cls, value: str | None) -> str | None:
        """No romaji reaches the learner — the plan bans it everywhere in the UI."""
        if value is not None and _LATIN.search(value):
            raise ValueError(f"latin letters in a learner-facing field: {value!r}")
        return value

    @field_validator("char", "example_word", "example_reading")
    @classmethod
    def _kana_only(cls, value: str | None) -> str | None:
        if value is not None and not _KANA_ONLY.match(value):
            raise ValueError(f"expected kana only, got {value!r}")
        return value


class KanaSeedFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    script: KanaScript
    kana: tuple[KanaSeedEntry, ...]

    @field_validator("kana")
    @classmethod
    def _consistent(cls, entries: tuple[KanaSeedEntry, ...]) -> tuple[KanaSeedEntry, ...]:
        if not entries:
            raise ValueError("empty seed file")
        orders = [e.group_order for e in entries]
        if len(set(orders)) != len(orders):
            raise ValueError("duplicate group_order")
        if orders != sorted(orders):
            raise ValueError("group_order must be ascending")
        chars = [e.char for e in entries]
        if len(set(chars)) != len(chars):
            raise ValueError("duplicate char")
        for entry in entries:
            if entry.example_word and entry.char not in entry.example_word:
                raise ValueError(f"example word {entry.example_word!r} does not contain {entry.char!r}")
        return entries


def load_seed(script: KanaScript, *, seed_dir: Path | None = None) -> KanaSeedFile:
    path = (seed_dir or SEED_DIR) / SEED_FILES[script]
    data = json.loads(path.read_text(encoding="utf-8"))
    parsed = KanaSeedFile.model_validate(data)
    if parsed.script != script:
        raise ValueError(f"{path.name} declares script={parsed.script}, expected {script}")
    return parsed


def load_all(*, seed_dir: Path | None = None) -> list[KanaSeedFile]:
    """Both scripts in curriculum order (hiragana first — its group_order range sorts ahead)."""
    files = [load_seed(script, seed_dir=seed_dir) for script in SEED_FILES]
    orders = [entry.group_order for f in files for entry in f.kana]
    if len(set(orders)) != len(orders):
        raise ValueError("group_order collides across scripts; katakana must not reuse hiragana's range")

    # The two scripts teach the same syllables in the same order, so their key sequences must match
    # exactly. ``romaji_key`` is what normalises a typed answer, and a learner typing the same sound
    # must be graded the same way whichever script is on screen.
    sequences = {f.script: [(e.row, e.romaji_key) for e in f.kana] for f in files}
    hira, kata = sequences[KanaScript.hiragana], sequences[KanaScript.katakana]
    if hira != kata:
        drift = [(a, b) for a, b in zip(hira, kata, strict=True) if a != b]
        raise ValueError(f"hiragana and katakana seeds are not parallel: {drift[:5]}")
    return files
