"""The vocabulary seed's validation rules, and what warm-audio decides to synthesise.

No database: these are the checks that must be able to fail in CI before a seed reaches one.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from app.content_pipeline.cli import app
from app.content_pipeline.vocab_seed import VocabSeedEntry, example_stems, load_vocab

runner = CliRunner()


def _entry(**overrides: Any) -> VocabSeedEntry:
    base: dict[str, Any] = {
        "slug": "vocab:mizu",
        "word": "水",
        "reading": "みず",
        "gloss_ru": "вода",
        "pos": "noun",
        "curriculum_order": 26,
        "tags": ("food",),
        "example": {"ja": "水をください。", "reading": "みずをください。", "gloss_ru": "Дайте воды."},
        "gloss_source": "claude",
        "gloss_review_status": "needs_review",
    }
    return VocabSeedEntry.model_validate(base | overrides)


def test_the_shipped_seed_validates() -> None:
    seed = load_vocab()
    assert len(seed.vocab) == 220
    assert [e.curriculum_order for e in seed.vocab] == list(range(1, 221))


def test_a_reading_may_not_contain_kanji() -> None:
    """The reading is what furigana renders and what the synthesiser is sent; kanji makes it a guess."""
    with pytest.raises(ValidationError, match="reading must be kana only"):
        _entry(reading="水ず")


def test_a_sentence_reading_may_not_contain_kanji() -> None:
    with pytest.raises(ValidationError, match="kana and punctuation only"):
        _entry(example={"ja": "水をください。", "reading": "水をください。", "gloss_ru": "Дайте воды."})


def test_no_romaji_anywhere_the_learner_reads() -> None:
    with pytest.raises(ValidationError, match="latin letters"):
        _entry(gloss_ru="voda")


def test_the_example_must_demonstrate_the_headword() -> None:
    with pytest.raises(ValidationError, match="does not contain"):
        _entry(word="本", example={"ja": "水をください。", "reading": "みずをください。", "gloss_ru": "Дайте воды."})


@pytest.mark.parametrize(
    ("word", "sentence"),
    [
        ("食べる", "朝ご飯を食べます。"),  # ichidan: 食べ
        ("行く", "毎日学校へ行きます。"),  # godan: 行
        ("高い", "この店は高いです。"),  # i-adjective: 高
        ("勉強する", "毎日日本語を勉強します。"),  # suru compound: 勉強
        ("来る", "友だちが来ます。"),  # irregular, but the kanji survives
    ],
)
def test_conjugated_examples_are_accepted(word: str, sentence: str) -> None:
    """A verb never appears in dictionary form, so a literal match would reject nearly every one."""
    entry = _entry(word=word, pos="verb", example={"ja": sentence, "reading": "かな。", "gloss_ru": "пример"})
    assert entry.word == word


def test_unknown_pos_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown pos"):
        _entry(pos="adjectiv")


def test_unknown_review_status_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown gloss_review_status"):
        _entry(gloss_review_status="ok")


def test_unknown_gloss_source_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown gloss_source"):
        _entry(gloss_source="chatgpt")


def test_example_stems_does_not_produce_the_empty_string() -> None:
    """`する` is two characters; stripping both would match every sentence ever written."""
    assert "" not in example_stems("する")
    assert "" not in example_stems("水")


def _seed_copy(tmp_path: Path) -> Path:
    seed_dir = tmp_path / "seed"
    shutil.copytree(Path(__file__).resolve().parents[2] / "data" / "seed", seed_dir)
    return seed_dir


def _rewrite_vocab(seed_dir: Path, mutate: Any) -> None:
    path = seed_dir / "vocab_core.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def test_check_passes_on_the_shipped_seeds(tmp_path: Path) -> None:
    result = runner.invoke(app, ["check", "--seed-dir", str(_seed_copy(tmp_path))])
    assert result.exit_code == 0, result.output
    assert "220 entries" in result.output


def test_check_fails_on_a_reading_containing_a_kanji(tmp_path: Path) -> None:
    seed_dir = _seed_copy(tmp_path)

    def mutate(data: dict[str, Any]) -> None:
        data["vocab"][25]["reading"] = "水ず"

    _rewrite_vocab(seed_dir, mutate)
    result = runner.invoke(app, ["check", "--seed-dir", str(seed_dir)])
    assert result.exit_code == 1
    assert "reading must be kana only" in result.output


def test_check_fails_on_a_duplicate_slug(tmp_path: Path) -> None:
    seed_dir = _seed_copy(tmp_path)

    def mutate(data: dict[str, Any]) -> None:
        data["vocab"][1]["slug"] = data["vocab"][0]["slug"]

    _rewrite_vocab(seed_dir, mutate)
    result = runner.invoke(app, ["check", "--seed-dir", str(seed_dir)])
    assert result.exit_code == 1
    assert "duplicate slug" in result.output


def test_check_fails_when_the_curriculum_is_reordered(tmp_path: Path) -> None:
    """File order and curriculum order are the same thing; a silent reorder is the failure."""
    seed_dir = _seed_copy(tmp_path)

    def mutate(data: dict[str, Any]) -> None:
        data["vocab"][0], data["vocab"][1] = data["vocab"][1], data["vocab"][0]

    _rewrite_vocab(seed_dir, mutate)
    result = runner.invoke(app, ["check", "--seed-dir", str(seed_dir)])
    assert result.exit_code == 1
    assert "ascending" in result.output


def test_warm_audio_sends_the_reading_and_never_the_written_headword() -> None:
    """日本 must never reach the API: isolated, the engine cannot know にほん from にっぽん."""
    from app.content_pipeline.warm_audio import seed_texts, vocab_texts

    texts = set(vocab_texts())
    assert "にほん" in texts
    assert "日本" not in texts

    # A sentence is the exception, and goes as written so は is read as *wa*.
    assert "日本語を勉強します。" in texts

    assert len(set(seed_texts())) == len(seed_texts()), "seed_texts must be deduplicated"
