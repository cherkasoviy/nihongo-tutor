"""Turning a learner's answer into an FSRS rating.

There are two graders because the two clients know different things. Typed and multiple-choice steps
know objectively whether the answer was right and how long it took (:func:`grade_auto`); chat
recognition cards only know which of three buttons the learner pressed, so the "Hard" band has to be
inferred from how long the answer stayed hidden (:func:`grade_self`).

Normalisation is deliberately generous about *script*: a kana card accepts either the character or
its Polivanov reading, and a learner who answers シ where the card holds し has recalled the item —
punishing the input method would teach nothing. It is not generous about *content*: a near-miss
earns ``Hard`` at best, never ``Good``, and a genuine miss is always ``Again``.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from typing import Final

import jaconv
from rapidfuzz.distance import Indel

from app.domain.srs import Rating

__all__ = [
    "DEFAULT_SLOW_MS",
    "DEFAULT_TYPO_RATIO",
    "MatchKind",
    "SelfGrade",
    "grade_auto",
    "grade_self",
    "match_answer",
    "normalize_answer",
]

# The plan's "reveal time >8 s" threshold, reused for typed answers so both graders draw the
# fast/slow line in the same place.
DEFAULT_SLOW_MS: Final = 8_000
DEFAULT_TYPO_RATIO: Final = 0.9


class MatchKind(enum.StrEnum):
    exact = "exact"
    typo = "typo"
    wrong = "wrong"


class SelfGrade(enum.StrEnum):
    """The three buttons under a chat recognition card."""

    forgot = "forgot"
    knew = "knew"
    easy = "easy"


def normalize_answer(raw: str) -> str:
    """Fold away everything about an answer that is not the answer.

    Half-width katakana is widened first because :func:`jaconv.kata2hira` only knows the full-width
    forms, so ``ｼ`` would otherwise survive as its own third script.
    """
    text = " ".join(raw.split())
    text = jaconv.h2z(text, kana=True, ascii=False, digit=False)
    text = jaconv.z2h(text, kana=False, ascii=True, digit=True)
    return str(jaconv.kata2hira(text)).casefold()


def match_answer(given: str, accepted: Sequence[str], *, typo_ratio: float = DEFAULT_TYPO_RATIO) -> MatchKind:
    """Classify ``given`` against every spelling the card will take.

    ``accepted`` holds the kana and its Cyrillic reading, so the closest candidate wins: a typo in
    the romanisation must not be measured against the kana it is nowhere near.
    """
    answer = normalize_answer(given)
    candidates = [normalize_answer(one) for one in accepted]
    if answer in candidates:
        return MatchKind.exact
    # Indel normalized_similarity is rapidfuzz's ``fuzz.ratio`` on a 0-1 scale, without the
    # divide-by-100 that would drift the threshold comparison off an exact 0.9.
    best = max((Indel.normalized_similarity(answer, one) for one in candidates), default=0.0)
    return MatchKind.typo if best >= typo_ratio else MatchKind.wrong


def grade_auto(
    match: MatchKind,
    *,
    elapsed_ms: int,
    hinted: bool = False,
    retried: bool = False,
    slow_ms: int = DEFAULT_SLOW_MS,
    claimed_easy: bool = False,
) -> Rating:
    """Rate an objectively gradable step.

    A hint or a retry means the memory was not there unaided, which is exactly what ``Hard`` says to
    FSRS. ``claimed_easy`` is the learner's optional third button and can only confirm an answer that was
    already clean and unassisted — it must never lift a typo, a hint or a slow recall into ``Easy``.
    """
    if match is MatchKind.wrong:
        return Rating.Again
    # ``>`` not ``>=``: an answer landing exactly on the threshold is still inside the fast band.
    if match is MatchKind.typo or hinted or retried or elapsed_ms > slow_ms:
        return Rating.Hard
    return Rating.Easy if claimed_easy else Rating.Good


def grade_self(choice: SelfGrade, *, reveal_ms: int, slow_ms: int = DEFAULT_SLOW_MS) -> Rating:
    """Rate a self-graded recognition card.

    Self-grading has no ``Hard`` button on purpose — three choices are as many as a learner will
    make honestly — so ``Hard`` is inferred from a "knew it" that took too long to arrive.
    """
    match choice:
        case SelfGrade.forgot:
            return Rating.Again
        case SelfGrade.easy:
            return Rating.Easy
        case SelfGrade.knew:
            return Rating.Hard if reveal_ms > slow_ms else Rating.Good
