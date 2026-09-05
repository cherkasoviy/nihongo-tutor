"""Interleaving constraints, degenerate inputs, and seeded determinism."""

from __future__ import annotations

import datetime as dt
import random
import uuid
from collections import Counter

import pytest

from app.db.models.learning import StepKind
from app.domain.interleave import (
    DEFAULT_MAX_RUN,
    DEFAULT_MIN_GAP_AFTER_INTRO,
    PlannedStep,
    interleave,
    make_seed,
    violations,
)

USER = uuid.UUID("7f3b1c0e-0000-7000-8000-00000000abcd")
OTHER_USER = uuid.UUID("7f3b1c0e-0000-7000-8000-00000000dcba")
LOCAL_DATE = dt.date(2026, 3, 14)

SEEDS = tuple(range(40))


def _reviews(count: int, kind: StepKind = StepKind.review_recog) -> list[PlannedStep]:
    return [PlannedStep(kind=kind, item_key=f"{kind}-{i}", card_id=uuid.uuid4()) for i in range(count)]


def _new_item(key: str, retest: StepKind) -> list[PlannedStep]:
    """The plan's chain for one new item: intro, immediate check, delayed cloze, wrap-up retest."""
    return [
        PlannedStep(kind=StepKind.intro_item, item_key=key, payload={"slug": key}),
        PlannedStep(kind=StepKind.review_recog, item_key=key),
        PlannedStep(kind=StepKind.cloze, item_key=key),
        PlannedStep(kind=retest, item_key=key, pinned_last=True),
    ]


NEW_ITEMS = (
    ("kana-a", StepKind.review_recog),
    ("kana-i", StepKind.review_prod),
    ("kana-u", StepKind.review_recog),
    ("kana-e", StepKind.review_prod),
)


def _session() -> list[PlannedStep]:
    """A realistic 17-minute day: warm-up reviews, four new items, listening, one output task."""
    steps = _reviews(12)
    for key, retest in NEW_ITEMS:
        steps += _new_item(key, retest)
    steps += _reviews(3, StepKind.listen_choose)
    steps.append(PlannedStep(kind=StepKind.shadow, item_key="output-1"))
    steps.append(PlannedStep(kind=StepKind.wrapup, item_key="summary", pinned_last=True))
    return steps


def _gated_retests(order: list[PlannedStep]) -> list[tuple[int, int, PlannedStep]]:
    """Every step owing its intro a gap, as ``(intro index, own index, step)``.

    The first step after an intro is the immediate check and is exempt, exactly as the module reads
    it, so this recomputes the classification instead of trusting a kind.
    """
    intros = {step.item_key: i for i, step in enumerate(order) if step.kind is StepKind.intro_item}
    out: list[tuple[int, int, PlannedStep]] = []
    for key, intro in intros.items():
        after = [i for i, step in enumerate(order) if step.item_key == key and i > intro]
        out += [(intro, i, order[i]) for i in after[1:]]
    return out


def _identities(steps: list[PlannedStep]) -> Counter[int]:
    return Counter(id(step) for step in steps)


def _signature(steps: list[PlannedStep]) -> tuple[tuple[str, str, bool], ...]:
    return tuple((step.kind.value, step.item_key, step.pinned_last) for step in steps)


@pytest.mark.parametrize("seed", SEEDS)
def test_mixed_session_satisfies_every_constraint(seed: int) -> None:
    assert violations(interleave(_session(), seed=seed)) == []


@pytest.mark.parametrize("seed", SEEDS)
def test_no_three_steps_of_the_same_kind_in_a_row(seed: int) -> None:
    order = interleave(_session(), seed=seed)
    kinds = [step.kind for step in order]
    runs = [kinds[i : i + DEFAULT_MAX_RUN + 1] for i in range(len(kinds) - DEFAULT_MAX_RUN)]
    assert not [run for run in runs if len(set(run)) == 1]


@pytest.mark.parametrize("seed", SEEDS)
def test_retests_land_at_least_five_steps_after_their_intro(seed: int) -> None:
    retests = _gated_retests(interleave(_session(), seed=seed))
    assert len(retests) == 8  # a delayed cloze and a wrap-up retest for each of the four new items
    for intro, i, _ in retests:
        assert i - intro >= DEFAULT_MIN_GAP_AFTER_INTRO


@pytest.mark.parametrize("seed", SEEDS)
def test_immediate_check_directly_follows_its_intro(seed: int) -> None:
    order = interleave(_session(), seed=seed)
    for i, step in enumerate(order):
        if step.kind is StepKind.intro_item:
            assert order[i + 1].item_key == step.item_key


@pytest.mark.parametrize("seed", SEEDS)
def test_wrapup_steps_stay_at_the_end(seed: int) -> None:
    order = interleave(_session(), seed=seed)
    assert [step.pinned_last for step in order][-5:] == [True] * 5
    assert not any(step.pinned_last for step in order[:-5])


@pytest.mark.parametrize("seed", SEEDS)
def test_every_step_appears_exactly_once(seed: int) -> None:
    steps = _session()
    order = interleave(steps, seed=seed)
    assert len(order) == len(steps)
    assert _identities(order) == _identities(steps)


def test_same_seed_replays_the_same_order() -> None:
    steps = _session()
    first = interleave(steps, seed=4242)
    second = interleave(steps, seed=4242)
    assert [id(step) for step in first] == [id(step) for step in second]


def test_rebuilt_input_with_the_same_seed_gives_the_same_order() -> None:
    """Reopening the session builds fresh PlannedStep objects; the sequence must not care."""
    assert _signature(interleave(_session(), seed=99)) == _signature(interleave(_session(), seed=99))


def test_different_seeds_generally_differ() -> None:
    orders = {_signature(interleave(_session(), seed=seed)) for seed in SEEDS}
    assert len(orders) >= len(SEEDS) - 2


def test_module_level_random_is_never_touched() -> None:
    random.seed(20260314)
    expected = [random.random() for _ in range(3)]
    random.seed(20260314)
    interleave(_session(), seed=1)
    assert [random.random() for _ in range(3)] == expected


def test_all_one_kind_terminates_and_reports_instead_of_raising() -> None:
    steps = _reviews(30)
    order = interleave(steps, seed=7)
    assert _identities(order) == _identities(steps)
    assert violations(order) == ["index 0: run of 30 review_recog steps (max 2)"]


def test_session_too_short_to_space_a_retest_still_returns_everything() -> None:
    steps = [
        PlannedStep(kind=StepKind.intro_item, item_key="solo"),
        PlannedStep(kind=StepKind.review_recog, item_key="solo"),
        PlannedStep(kind=StepKind.wrapup, item_key="solo", pinned_last=True),
    ]
    order = interleave(steps, seed=3)
    assert _signature(order) == _signature(steps)
    assert violations(order) == [
        "index 2: wrapup retests solo only 2 steps after its intro (need 5)",
    ]


def test_intro_leads_its_chain_whatever_order_the_caller_used() -> None:
    """The planner may assemble a chain retest-first; the item still cannot be tested before it exists."""
    steps = [
        PlannedStep(kind=StepKind.cloze, item_key="kana-a"),
        PlannedStep(kind=StepKind.review_recog, item_key="kana-a"),
        PlannedStep(kind=StepKind.intro_item, item_key="kana-a"),
        *_reviews(5, StepKind.listen_choose),
        *_reviews(4, StepKind.shadow),
    ]
    order = interleave(steps, seed=5)
    own = [i for i, step in enumerate(order) if step.item_key == "kana-a"]
    # Only the intro moves; behind it the caller's order stands, so the cloze is now the check.
    assert [order[i].kind for i in own] == [StepKind.intro_item, StepKind.cloze, StepKind.review_recog]
    assert own[1] == own[0] + 1
    assert own[2] - own[0] >= DEFAULT_MIN_GAP_AFTER_INTRO
    assert violations(order) == []


def _kana_day(due_recog: int, due_prod: int, new_items: int) -> list[PlannedStep]:
    """The chain ``session_service`` actually assembles for a Phase 1 kana day.

    Every new syllable gets an intro, an immediate recognition check, a production drill, and a
    recognition retest pinned to the wrap-up -- the one grade of the day that reaches FSRS.
    """
    steps = [PlannedStep(kind=StepKind.review_recog, item_key=f"due-r{i}") for i in range(due_recog)]
    steps += [PlannedStep(kind=StepKind.review_prod, item_key=f"due-p{i}") for i in range(due_prod)]
    for n in range(new_items):
        key = f"new-{n}"
        steps += [
            PlannedStep(kind=StepKind.intro_item, item_key=key),
            PlannedStep(kind=StepKind.review_recog, item_key=key),
            PlannedStep(kind=StepKind.review_prod, item_key=key),
            PlannedStep(kind=StepKind.wrapup, item_key=key, pinned_last=True),
        ]
    return steps


def _body_violations(order: list[PlannedStep]) -> list[str]:
    """Everything broken in the interleaved body, excluding the pinned wrap-up's own run.

    The wrap-up is every-step-one-kind by construction, so ``violations`` always reports a run there
    on a day with three or more new items. That is the section working as designed. Filtering *all*
    run messages to get past it, though, is what makes a test unable to fail on the runs that are
    genuinely avoidable, so this keeps the body's.
    """
    first_pinned = next((i for i, step in enumerate(order) if step.pinned_last), len(order))
    return [
        message
        for message in violations(order)
        if "run of" not in message or int(message.split()[1].rstrip(":")) < first_pinned
    ]


KANA_DAYS = [(0, 0, 5), (6, 2, 5), (10, 6, 5), (14, 8, 2), (16, 10, 0), (20, 12, 2), (12, 4, 4)]


# Days with enough due reviews to interleave against; a real learner reaches these within a week.
WELL_STOCKED_DAYS = [(14, 8, 2), (16, 10, 0), (20, 12, 2), (12, 4, 4)]
# Days too thin to satisfy everything: the start of the bootcamp, before reviews accumulate.
THIN_DAYS = [(0, 0, 5), (2, 0, 6), (6, 2, 5)]


@pytest.mark.parametrize("shape", KANA_DAYS)
@pytest.mark.parametrize("seed", SEEDS[:10])
def test_spacing_holds_on_every_real_session_shape(shape: tuple[int, int, int], seed: int) -> None:
    """Spacing is the constraint that carries the pedagogy, and it holds on every shape.

    Runs are the negotiable half; a retest arriving too soon after its intro is not, because it
    turns an effortful recall back into an echo.
    """
    order = interleave(_kana_day(*shape), seed=seed)
    for intro, i, _ in _gated_retests(order):
        assert i - intro >= DEFAULT_MIN_GAP_AFTER_INTRO


@pytest.mark.parametrize("shape", WELL_STOCKED_DAYS)
@pytest.mark.parametrize("seed", SEEDS[:10])
def test_a_well_stocked_day_breaks_nothing_in_the_body(shape: tuple[int, int, int], seed: int) -> None:
    """Once the review queue can pad the session, every constraint is satisfiable — and satisfied."""
    assert _body_violations(interleave(_kana_day(*shape), seed=seed)) == []


@pytest.mark.parametrize("shape", THIN_DAYS)
def test_a_thin_day_degrades_gracefully_instead_of_failing(shape: tuple[int, int, int]) -> None:
    """Day one has five intros and almost nothing to interleave them against.

    Five production drills each owe their intro five steps, and a fifteen-step block cannot pay all
    five debts. The module promises a total ordering, not a perfect one, so the contract here is
    that every step comes back exactly once and the shortfall is reported rather than hidden.
    """
    plan = _kana_day(*shape)
    for seed in SEEDS[:20]:
        order = interleave(plan, seed=seed)
        assert _identities(order) == _identities(plan), "steps must be permuted, never dropped"
        assert sorted(_signature(order)) == sorted(_signature(plan))


def test_the_repair_pass_keeps_avoidable_runs_rare_on_thin_days() -> None:
    """A regression guard on the repair pass itself.

    The greedy alone leaves an avoidable run in the body of roughly one thin day in thirteen; the
    relocation pass brings that under one in fifty. Asserting a rate rather than zero is the honest
    bound — zero is unreachable while the first days have nothing to interleave against — but it is
    tight enough that deleting the repair fails this test.
    """
    shapes = [(6, 2, 5), (8, 3, 6), (10, 6, 5), (4, 1, 5)]
    total = broken = 0
    for shape in shapes:
        for seed in range(100):
            total += 1
            if _body_violations(interleave(_kana_day(*shape), seed=seed)):
                broken += 1
    assert broken / total <= 0.02, f"{broken}/{total} thin days had an avoidable run in the body"


def test_single_kind_wrapup_block_is_reported_not_hidden() -> None:
    """``violations`` describes the order it is handed; what is tolerable is the caller's call.

    ``session_service`` pins one wrap-up retest per new item, so the block is one kind by
    construction and interleaving cannot fix it — only varying the retest direction could. The
    ordering stays honest and reports the run rather than quietly excusing pinned steps.
    """
    order = interleave(_kana_day(6, 2, 5), seed=1)
    assert [step.pinned_last for step in order][-5:] == [True] * 5
    assert {step.kind for step in order[-5:]} == {StepKind.wrapup}
    assert [message for message in violations(order) if "run of" in message]


def test_empty_input() -> None:
    assert interleave([], seed=1) == []
    assert violations([]) == []


def test_single_step() -> None:
    steps = [PlannedStep(kind=StepKind.shadow, item_key="only")]
    assert interleave(steps, seed=1) == steps
    assert violations(steps) == []


def test_custom_limits_are_honoured() -> None:
    steps = _reviews(6, StepKind.cloze) + _reviews(6, StepKind.listen_choose)
    order = interleave(steps, seed=11, max_run=1, min_gap_after_intro=0)
    assert violations(order, max_run=1, min_gap_after_intro=0) == []


def test_violations_flags_a_hand_built_bad_order() -> None:
    steps = [
        PlannedStep(kind=StepKind.wrapup, item_key="kana-a", pinned_last=True),
        PlannedStep(kind=StepKind.intro_item, item_key="kana-a"),
        PlannedStep(kind=StepKind.review_recog, item_key="kana-a"),
        PlannedStep(kind=StepKind.cloze, item_key="kana-a"),
        PlannedStep(kind=StepKind.cloze, item_key="c-1"),
        PlannedStep(kind=StepKind.cloze, item_key="c-2"),
    ]
    reported = violations(steps)
    assert reported == [
        "index 0: wrapup for kana-a precedes its intro at index 1",
        "index 0: wrap-up wrapup for kana-a is followed by regular steps",
        "index 3: run of 3 cloze steps (max 2)",
        "index 3: cloze retests kana-a only 2 steps after its intro (need 5)",
    ]


def test_make_seed_is_stable_across_processes() -> None:
    assert make_seed(USER, LOCAL_DATE) == make_seed(USER, LOCAL_DATE)
    # Golden value: the seed is hashed, not derived from the salted builtin hash().
    assert make_seed(USER, LOCAL_DATE) == 3956367585834080059


def test_make_seed_varies_by_user_and_day() -> None:
    assert make_seed(USER, LOCAL_DATE) != make_seed(OTHER_USER, LOCAL_DATE)
    assert make_seed(USER, LOCAL_DATE) != make_seed(USER, LOCAL_DATE + dt.timedelta(days=1))
