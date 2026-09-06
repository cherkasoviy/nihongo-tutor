"""Choose wrong answers for multiple-choice steps.

The plan calls for distractors "drawn algorithmically from known items" — never random noise. A
choice between あ and ヲ teaches nothing; a choice between さ and ち is the actual discrimination a
beginner has to make. So candidates are ranked: characters known to be visually confusable first,
then same-row neighbours (which share a consonant and are confused by sound), then anything else the
learner has already met.

Pure and deterministic: the caller supplies the seeded RNG, so reopening a session shows the same grid.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

# Pairs beginners genuinely mix up: near-identical strokes, or the same stroke order mirrored.
# Symmetric — the lookup below closes it in both directions.
_CONFUSABLE_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    ("さ", "ち"),
    ("ぬ", "め"),
    ("れ", "わ"),
    ("れ", "ね"),
    ("わ", "ね"),
    ("る", "ろ"),
    ("は", "ほ"),
    ("は", "ま"),
    ("い", "り"),
    ("こ", "に"),
    ("き", "さ"),
    ("す", "む"),
    ("あ", "お"),
    ("た", "な"),
    ("ぐ", "べ"),
    ("し", "つ"),
    ("そ", "ろ"),
    ("ふ", "ぶ"),
    ("シ", "ツ"),
    ("ソ", "ン"),
    ("シ", "ン"),
    ("ソ", "ツ"),
    ("ク", "ケ"),
    ("ク", "タ"),
    ("ス", "ヌ"),
    ("ノ", "メ"),
    ("フ", "ワ"),
    ("ウ", "ワ"),
    ("コ", "ユ"),
    ("ホ", "ボ"),
    ("チ", "テ"),
    ("ナ", "メ"),
    ("ハ", "ヘ"),
    ("マ", "ム"),
    ("ヲ", "オ"),
    ("レ", "ル"),
)


def _build_confusables() -> dict[str, tuple[str, ...]]:
    table: dict[str, list[str]] = {}
    for left, right in _CONFUSABLE_PAIRS:
        table.setdefault(left, []).append(right)
        table.setdefault(right, []).append(left)
    return {k: tuple(v) for k, v in table.items()}


CONFUSABLES: Final[dict[str, tuple[str, ...]]] = _build_confusables()


@dataclass(frozen=True, slots=True)
class Candidate:
    """One option the learner could be offered.

    ``label`` is what appears on the button, which differs by direction: the glyph when the learner
    is asked for a reading, the reading when they are asked for a glyph. ``glyph`` always carries the
    character itself, because confusability is a property of the shape and has to survive relabelling
    — otherwise a recognition grid would look up "си" in a table keyed by シ and never find anything.
    """

    key: str
    label: str
    row: str
    glyph: str = ""
    reading: str = ""

    @property
    def shape(self) -> str:
        """The character this option stands for, whatever the button happens to say."""
        return self.glyph or self.label

    @property
    def answer(self) -> str:
        """What this option would mean as an answer, for deciding whether two options collide."""
        return self.reading or self.label


def pick_distractors(
    correct: Candidate,
    pool: Sequence[Candidate],
    *,
    count: int,
    rng: random.Random,
) -> list[Candidate]:
    """Pick ``count`` wrong answers for ``correct``, best discriminators first.

    Falls short (returns fewer than ``count``) only when the pool genuinely cannot supply enough
    distinct options — early in the kana bootcamp the learner has met barely a handful of syllables.
    Callers render however many they get rather than padding with characters the learner has never seen.
    """
    if count <= 0:
        return []

    seen = {correct.key}
    # Polivanov gives genuinely different characters the same reading — お and を are both «о», じ and
    # ぢ both «дзи» — and that collides in *both* directions, for different reasons:
    #
    #   recognition ("how is お read?")   two buttons would literally both say «о»
    #   production  ("which reads «о»?")  お and を are two honest answers to one question
    #
    # So an option is rejected when either its visible label or the reading behind it matches the
    # answer's. Either way the learner would be marked wrong for being right.
    used_labels = {correct.label}
    used_answers = {correct.answer}
    tiers: list[list[Candidate]] = [[], [], []]
    confusable = set(CONFUSABLES.get(correct.shape, ()))

    for candidate in pool:
        if candidate.key in seen or candidate.label in used_labels or candidate.answer in used_answers:
            continue
        seen.add(candidate.key)
        used_labels.add(candidate.label)
        used_answers.add(candidate.answer)
        if candidate.shape in confusable:
            tiers[0].append(candidate)
        elif candidate.row == correct.row:
            tiers[1].append(candidate)
        else:
            tiers[2].append(candidate)

    chosen: list[Candidate] = []
    for tier in tiers:
        if len(chosen) >= count:
            break
        rng.shuffle(tier)
        chosen.extend(tier[: count - len(chosen)])
    return chosen


def build_choices(
    correct: Candidate,
    pool: Sequence[Candidate],
    *,
    options: int,
    rng: random.Random,
) -> tuple[list[Candidate], int]:
    """Return the shuffled option list and the index of the correct answer within it."""
    distractors = pick_distractors(correct, pool, count=max(0, options - 1), rng=rng)
    choices = [correct, *distractors]
    rng.shuffle(choices)
    return choices, choices.index(correct)
