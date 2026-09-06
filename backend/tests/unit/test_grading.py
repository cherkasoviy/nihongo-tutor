from __future__ import annotations

import pytest

from app.domain.grading import (
    DEFAULT_SLOW_MS,
    MatchKind,
    SelfGrade,
    grade_auto,
    grade_self,
    match_answer,
    normalize_answer,
)
from app.domain.srs import Rating

# The kana card for し: the learner may type the character in either script or the Polivanov reading.
SHI = ("し", "си")
# A longer card, for checking that a near-miss is scored against the candidate it is near.
KONNICHIWA = ("こんにちは", "коннитива")

# Ten characters against ten with a single substitution: Indel similarity is exactly 0.9, the
# threshold itself, so these pin the boundary rather than approach it.
TEN_KANA = "あいうえおかきくけこ"
TEN_KANA_ONE_OFF = "あいうえおかきくけさ"
TEN_KANA_TWO_OFF = "あいうえおかきくさし"


# ---------------------------------------------------------------------------
# normalize_answer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("し", "し"),
        ("シ", "し"),  # katakana folds to hiragana
        ("ｼ", "し"),  # half-width katakana too
        ("  し  ", "し"),
        ("\tシ\n", "し"),
        ("か　き", "か き"),  # ideographic space collapses like any other
        ("こん  にち　は", "こん にち は"),
        ("ＳＩ", "si"),  # full-width latin narrows, then casefolds
        ("Ｎ５", "n5"),
        ("Си", "си"),  # Cyrillic casefolds as well
        ("", ""),
    ],
)
def test_normalize_answer(raw: str, expected: str) -> None:
    assert normalize_answer(raw) == expected


def test_normalize_answer_is_idempotent() -> None:
    once = normalize_answer(" ｼ　Ｎ５ ")
    assert normalize_answer(once) == once


# ---------------------------------------------------------------------------
# match_answer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "accepted", "expected"),
    [
        ("し", SHI, MatchKind.exact),
        ("シ", SHI, MatchKind.exact),  # either script is a full recall, not a typo
        ("ｼ", SHI, MatchKind.exact),
        ("  し ", SHI, MatchKind.exact),
        ("си", SHI, MatchKind.exact),  # the Polivanov reading is just as acceptable
        ("СИ", SHI, MatchKind.exact),
        ("си　", SHI, MatchKind.exact),
        ("シ", ("し",), MatchKind.exact),
        ("つ", SHI, MatchKind.wrong),  # a different kana is a miss, not a near-miss
        ("ци", SHI, MatchKind.wrong),
        ("", SHI, MatchKind.wrong),
        ("し", (), MatchKind.wrong),  # a card with nothing to match against never says exact
        (TEN_KANA_ONE_OFF, (TEN_KANA,), MatchKind.typo),  # ratio exactly 0.9 -> typo
        (TEN_KANA_TWO_OFF, (TEN_KANA,), MatchKind.wrong),  # ratio 0.8, just under
        # A doubled letter in the reading: near the Cyrillic candidate, nowhere near the kana one.
        ("конннитива", KONNICHIWA, MatchKind.typo),
        ("коннитива", KONNICHIWA, MatchKind.exact),
        ("привет", KONNICHIWA, MatchKind.wrong),
    ],
)
def test_match_answer(given: str, accepted: tuple[str, ...], expected: MatchKind) -> None:
    assert match_answer(given, accepted) is expected


def test_typo_threshold_is_inclusive() -> None:
    assert match_answer(TEN_KANA_ONE_OFF, (TEN_KANA,), typo_ratio=0.9) is MatchKind.typo


def test_typo_threshold_can_be_tightened() -> None:
    """A ratio just above the exact 0.9 similarity flips the same answer to wrong."""
    assert match_answer(TEN_KANA_ONE_OFF, (TEN_KANA,), typo_ratio=0.91) is MatchKind.wrong


# ---------------------------------------------------------------------------
# grade_auto: the whole table in one place
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("match", "elapsed_ms", "hinted", "retried", "claimed_easy", "expected"),
    [
        # Clean and fast.
        (MatchKind.exact, 1_200, False, False, False, Rating.Good),
        (MatchKind.exact, 0, False, False, False, Rating.Good),
        (MatchKind.exact, DEFAULT_SLOW_MS, False, False, False, Rating.Good),  # exactly on the line
        (MatchKind.exact, DEFAULT_SLOW_MS + 1, False, False, False, Rating.Hard),  # one ms over
        (MatchKind.exact, 30_000, False, False, False, Rating.Hard),
        # Assistance downgrades a correct answer.
        (MatchKind.exact, 1_200, True, False, False, Rating.Hard),
        (MatchKind.exact, 1_200, False, True, False, Rating.Hard),
        (MatchKind.exact, 1_200, True, True, False, Rating.Hard),
        # claimed_easy lifts only the clean, fast, unassisted answer.
        (MatchKind.exact, 1_200, False, False, True, Rating.Easy),
        (MatchKind.exact, DEFAULT_SLOW_MS, False, False, True, Rating.Easy),
        (MatchKind.exact, DEFAULT_SLOW_MS + 1, False, False, True, Rating.Hard),
        (MatchKind.exact, 1_200, True, False, True, Rating.Hard),
        (MatchKind.exact, 1_200, False, True, True, Rating.Hard),
        # A typo is Hard however it is dressed up.
        (MatchKind.typo, 1_200, False, False, False, Rating.Hard),
        (MatchKind.typo, 1_200, False, False, True, Rating.Hard),
        (MatchKind.typo, DEFAULT_SLOW_MS + 1, False, False, False, Rating.Hard),
        (MatchKind.typo, 1_200, True, True, False, Rating.Hard),
        # Wrong is Again however it is dressed up.
        (MatchKind.wrong, 1_200, False, False, False, Rating.Again),
        (MatchKind.wrong, 0, False, False, True, Rating.Again),
        (MatchKind.wrong, DEFAULT_SLOW_MS + 1, True, True, True, Rating.Again),
    ],
)
def test_grade_auto_table(
    match: MatchKind,
    elapsed_ms: int,
    hinted: bool,
    retried: bool,
    claimed_easy: bool,
    expected: Rating,
) -> None:
    assert (
        grade_auto(match, elapsed_ms=elapsed_ms, hinted=hinted, retried=retried, claimed_easy=claimed_easy) is expected
    )


@pytest.mark.parametrize("hinted", [False, True])
@pytest.mark.parametrize("retried", [False, True])
@pytest.mark.parametrize("claimed_easy", [False, True])
@pytest.mark.parametrize("elapsed_ms", [0, 1_200, DEFAULT_SLOW_MS, DEFAULT_SLOW_MS + 1, 60_000])
def test_wrong_is_never_graded_above_again(hinted: bool, retried: bool, claimed_easy: bool, elapsed_ms: int) -> None:
    rating = grade_auto(
        MatchKind.wrong, elapsed_ms=elapsed_ms, hinted=hinted, retried=retried, claimed_easy=claimed_easy
    )
    assert rating is Rating.Again


@pytest.mark.parametrize("match", list(MatchKind))
def test_claimed_easy_never_downgrades(match: MatchKind) -> None:
    """Whatever the answer, claiming it was easy can only ever help."""
    plain = grade_auto(match, elapsed_ms=1_200)
    claimed = grade_auto(match, elapsed_ms=1_200, claimed_easy=True)
    assert claimed >= plain


def test_grade_auto_honours_a_custom_slow_threshold() -> None:
    assert grade_auto(MatchKind.exact, elapsed_ms=3_000, slow_ms=2_000) is Rating.Hard
    assert grade_auto(MatchKind.exact, elapsed_ms=2_000, slow_ms=2_000) is Rating.Good


# ---------------------------------------------------------------------------
# grade_self
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("choice", "reveal_ms", "expected"),
    [
        (SelfGrade.forgot, 200, Rating.Again),
        (SelfGrade.forgot, DEFAULT_SLOW_MS + 1, Rating.Again),  # a slow miss is still just a miss
        (SelfGrade.knew, 0, Rating.Good),
        (SelfGrade.knew, 2_500, Rating.Good),
        (SelfGrade.knew, DEFAULT_SLOW_MS, Rating.Good),  # exactly on the line
        (SelfGrade.knew, DEFAULT_SLOW_MS + 1, Rating.Hard),  # one ms over
        (SelfGrade.knew, 45_000, Rating.Hard),
        (SelfGrade.easy, 200, Rating.Easy),
        (SelfGrade.easy, DEFAULT_SLOW_MS + 1, Rating.Easy),  # the learner's own call wins
    ],
)
def test_grade_self_table(choice: SelfGrade, reveal_ms: int, expected: Rating) -> None:
    assert grade_self(choice, reveal_ms=reveal_ms) is expected


def test_grade_self_honours_a_custom_slow_threshold() -> None:
    assert grade_self(SelfGrade.knew, reveal_ms=3_000, slow_ms=2_000) is Rating.Hard
    assert grade_self(SelfGrade.knew, reveal_ms=2_000, slow_ms=2_000) is Rating.Good


@pytest.mark.parametrize("choice", list(SelfGrade))
def test_grade_self_covers_every_button(choice: SelfGrade) -> None:
    assert grade_self(choice, reveal_ms=1_000) in set(Rating)


# ---------------------------------------------------------------------------
# End to end: a typed kana answer through both halves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("シ", Rating.Good),  # right character, wrong script, fast
        ("  си ", Rating.Good),
        ("つ", Rating.Again),
    ],
)
def test_typed_kana_answer_end_to_end(typed: str, expected: Rating) -> None:
    assert grade_auto(match_answer(typed, SHI), elapsed_ms=1_500) is expected
