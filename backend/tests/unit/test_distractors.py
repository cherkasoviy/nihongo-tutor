"""Choosing wrong answers.

Two properties matter more than the ranking itself, and both were real bugs before these tests
existed: an option must never be indistinguishable from the correct answer, and confusability must
survive the relabelling that the recognition direction performs.
"""

from __future__ import annotations

import random

import pytest

from app.domain.distractors import CONFUSABLES, Candidate, build_choices, pick_distractors


def _rng() -> random.Random:
    return random.Random(20260406)


# Polivanov gives these pairs the same Cyrillic reading despite being different characters.
HOMOPHONES = [("お", "を", "о"), ("じ", "ぢ", "дзи"), ("ず", "づ", "дзу"), ("オ", "ヲ", "о")]


@pytest.mark.parametrize(("left", "right", "reading"), HOMOPHONES)
def test_a_distractor_never_repeats_the_correct_answers_label(left: str, right: str, reading: str) -> None:
    """お and を are both «о». Offering both marks a learner wrong for reading correctly."""
    correct = Candidate(key="a", label=reading, row="a", glyph=left)
    pool = [
        Candidate(key="b", label=reading, row="wa", glyph=right),  # the homophone
        Candidate(key="c", label="ка", row="ka", glyph="か"),
        Candidate(key="d", label="са", row="sa", glyph="さ"),
        Candidate(key="e", label="та", row="ta", glyph="た"),
    ]
    choices, index = build_choices(correct, pool, options=4, rng=_rng())
    labels = [c.label for c in choices]

    assert len(labels) == len(set(labels)), f"duplicate labels in the grid: {labels}"
    assert labels[index] == reading
    assert labels.count(reading) == 1


def test_confusability_is_looked_up_on_the_glyph_not_the_label() -> None:
    """The recognition direction relabels every option to its reading before building the grid.

    If the lookup used the label it would ask the table for «си», find nothing, and silently fall
    back to same-row ordering — losing the whole point of ranking シ against ツ.
    """
    correct = Candidate(key="shi", label="си", row="sa", glyph="シ")
    tsu = Candidate(key="tsu", label="цу", row="ta", glyph="ツ")
    pool = [
        Candidate(key="ka", label="ка", row="ka", glyph="カ"),
        Candidate(key="ku", label="ку", row="ka", glyph="ク"),
        tsu,
        Candidate(key="ho", label="хо", row="ha", glyph="ホ"),
    ]
    picked = pick_distractors(correct, pool, count=1, rng=_rng())

    assert "ツ" in CONFUSABLES["シ"]
    assert [c.key for c in picked] == ["tsu"], "the visually confusable option must rank first"


def test_confusable_options_outrank_same_row_which_outranks_the_rest() -> None:
    correct = Candidate(key="sa", label="са", row="sa", glyph="さ")
    confusable = Candidate(key="chi", label="ти", row="ta", glyph="ち")  # さ/ち is a listed pair
    same_row = Candidate(key="su", label="су", row="sa", glyph="す")
    unrelated = Candidate(key="ho", label="хо", row="ha", glyph="ほ")

    picked = pick_distractors(correct, [unrelated, same_row, confusable], count=3, rng=_rng())
    assert [c.key for c in picked] == ["chi", "su", "ho"]


def test_the_correct_answer_is_never_offered_as_its_own_distractor() -> None:
    correct = Candidate(key="a", label="а", row="a", glyph="あ")
    other = Candidate(key="i", label="и", row="a", glyph="い")
    picked = pick_distractors(correct, [correct, other], count=2, rng=_rng())
    assert [c.key for c in picked] == ["i"]


def test_a_thin_pool_yields_a_short_grid_rather_than_unseen_characters() -> None:
    """Day one of the bootcamp: the learner has met two syllables, so two options is honest."""
    correct = Candidate(key="a", label="а", row="a", glyph="あ")
    pool = [Candidate(key="i", label="и", row="a", glyph="い")]
    choices, index = build_choices(correct, pool, options=4, rng=_rng())
    assert len(choices) == 2
    assert choices[index].key == "a"


def test_the_same_seed_builds_the_same_grid() -> None:
    correct = Candidate(key="a", label="а", row="a", glyph="あ")
    pool = [Candidate(key=str(i), label=f"л{i}", row="ka", glyph=chr(0x30A0 + i)) for i in range(8)]
    first = build_choices(correct, pool, options=4, rng=random.Random(11))
    second = build_choices(correct, pool, options=4, rng=random.Random(11))
    assert [c.key for c in first[0]] == [c.key for c in second[0]]
    assert first[1] == second[1]


def test_the_confusable_table_is_symmetric() -> None:
    for glyph, partners in CONFUSABLES.items():
        for partner in partners:
            assert glyph in CONFUSABLES[partner], f"{glyph}/{partner} is only listed one way"


def test_requesting_no_distractors_is_not_an_error() -> None:
    correct = Candidate(key="a", label="а", row="a", glyph="あ")
    assert pick_distractors(correct, [], count=0, rng=_rng()) == []
    assert pick_distractors(correct, [], count=3, rng=_rng()) == []
