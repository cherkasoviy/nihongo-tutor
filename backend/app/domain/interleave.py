"""Ordering the steps of one session.

The planner decides *which* steps a day contains; this module decides the order they are shown in.
Two pedagogical constraints from the plan shape it:

* **Interleaving** — at most ``max_run`` steps of the same :class:`StepKind` in a row, so the learner
  keeps switching retrieval modes instead of blocking through one drill.
* **Expanding spacing inside the session** — a freshly introduced item gets its check straight away,
  and every later retest of it sits at least ``min_gap_after_intro`` steps behind the intro, so the
  second recall is already effortful rather than an echo.

Both are best effort, never guarantees. A session that is nothing but ``review_recog`` cannot avoid
runs, and a short session cannot put five steps between an intro and its wrap-up retest, so
:func:`interleave` always terminates and returns a total ordering instead of raising or searching
for an arrangement that does not exist. :func:`violations` reports what a given order breaks, so a
caller (or a test) can assert on the constraints directly rather than trusting the search.

Order is a pure function of the input and the seed. :func:`make_seed` derives one from the learner
and their local date, so reopening today's session replays the same sequence. Nothing here touches
``random``'s module-level generator, whose state is shared with the rest of the process.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import random
import uuid
from collections import Counter, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from app.db.models.learning import StepKind

__all__ = [
    "DEFAULT_MAX_RUN",
    "DEFAULT_MIN_GAP_AFTER_INTRO",
    "PlannedStep",
    "interleave",
    "make_seed",
    "violations",
]

DEFAULT_MAX_RUN: Final = 2
DEFAULT_MIN_GAP_AFTER_INTRO: Final = 5

# Where a step sits in its item's chain. Only ``_GATED`` and above owe the intro any distance:
# the immediate check is meant to be immediate.
_UNGATED: Final = -1
_INTRO: Final = 0
_CHECK: Final = 1
_GATED: Final = 2


@dataclass(frozen=True, slots=True)
class PlannedStep:
    """One step of a session, before it becomes a ``session_steps`` row.

    ``item_key`` is what ties an intro to its later retests; anything stable within the session works
    (an item UUID as a string, a kana slug). Steps sharing a key keep the relative order they were
    given in -- except that an intro always leads its own chain -- so the caller expresses the
    pedagogical chain and this module only decides the spacing between its links.
    """

    kind: StepKind
    item_key: str
    card_id: uuid.UUID | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    pinned_last: bool = False


def make_seed(user_id: uuid.UUID, local_date: dt.date) -> int:
    """A stable 64-bit seed for one learner-day.

    Hashed rather than combined arithmetically because ``hash()`` on strings is salted per process:
    the same day has to replay identically after a restart, not just within one worker.
    """
    material = f"{user_id}:{local_date.isoformat()}".encode()
    return int.from_bytes(hashlib.blake2b(material, digest_size=8).digest(), "big")


def interleave(
    steps: Iterable[PlannedStep],
    *,
    seed: int,
    max_run: int = DEFAULT_MAX_RUN,
    min_gap_after_intro: int = DEFAULT_MIN_GAP_AFTER_INTRO,
) -> list[PlannedStep]:
    """Order ``steps`` under the run and spacing constraints, deterministically for ``seed``.

    Returns exactly the input steps, each once, as a permutation: ``pinned_last`` steps last, the
    rest arranged greedily. The greedy pass never backtracks, so the result is best effort — when no
    step can satisfy every constraint the least important one is dropped for that slot (spacing
    first, then the run limit) rather than failing. Run :func:`violations` on the result if the
    caller needs to know; a degenerate session (all one kind, or too short to space a retest) will
    always report some.
    """
    plan = list(steps)
    if not plan:
        return []

    max_run = max(1, max_run)
    min_gap_after_intro = max(0, min_gap_after_intro)

    chains = _chains(plan)
    roles = _roles(plan, chains)
    free = [i for i, step in enumerate(plan) if not step.pinned_last]
    pinned = [i for i, step in enumerate(plan) if step.pinned_last]
    deadlines = _intro_deadlines(plan, roles, chains, free_slots=len(free), min_gap_after_intro=min_gap_after_intro)

    # Seeded on purpose, never for secrecy: the same learner-day must replay identically.
    rng = random.Random(seed)  # noqa: S311
    out: list[int] = []
    intro_at: dict[str, int] = {}
    for chunk in (free, pinned):
        _arrange(
            plan,
            roles,
            chains,
            chunk,
            out,
            intro_at,
            deadlines,
            rng,
            max_run=max_run,
            min_gap_after_intro=min_gap_after_intro,
        )

    order = [plan[i] for i in out]
    _repair_runs(order, free_slots=len(free), max_run=max_run, min_gap_after_intro=min_gap_after_intro)
    return order


def _repair_runs(
    order: list[PlannedStep],
    *,
    free_slots: int,
    max_run: int,
    min_gap_after_intro: int,
) -> None:
    """Break up runs the greedy could have avoided. Mutates ``order`` in place.

    The greedy pass places one step at a time and never backtracks, so production drills — which
    only become eligible five steps after their intro — tend to pile up at the tail of the free
    block even when a due review was available to separate them.

    The repair moves a step to a new position rather than swapping two: a swap would drag the
    production drill *earlier* and break the very spacing that stranded it, which is why only a
    relocation can help. Every candidate move is scored with :func:`violations` and kept only if it
    strictly improves, so the pass can only ever make an order better, and it stops as soon as
    nothing does. Deterministic (positional candidate order) and bounded, so the result still
    depends on nothing but the seed.
    """
    if free_slots < max_run + 1:
        return

    def score() -> int:
        """Only the defects a relocation inside the free block could remove.

        The pinned wrap-up is every-step-one-kind by construction, so ``violations`` will always
        report a run there on a normal day. Counting it would make the score bottom out above zero
        and mask whether the body actually improved, so the repair looks at the body's runs and at
        the spacing rules, which a relocation genuinely can break or fix.
        """
        body = [step for step in order if not step.pinned_last]
        return len(_run_violations(body, max_run)) + len(_spacing_violations(order, min_gap_after_intro))

    current = score()
    for _ in range(free_slots):  # a hard bound; each pass strictly improves or stops
        if not current:
            return
        improved = False
        for i in range(free_slots):
            for j in range(free_slots):
                if i == j:
                    continue
                order.insert(j, order.pop(i))
                after = score()
                if after < current:
                    current = after
                    improved = True
                    break
                order.insert(i, order.pop(j))  # put it back
            if improved:
                break
        if not improved:
            return


def violations(
    order: Sequence[PlannedStep],
    *,
    max_run: int = DEFAULT_MAX_RUN,
    min_gap_after_intro: int = DEFAULT_MIN_GAP_AFTER_INTRO,
) -> list[str]:
    """Every constraint the given order breaks, as one message per offending index.

    Deliberately independent of :func:`interleave`: it reads only the sequence handed to it, so a
    caller can check steps loaded back from the database the same way a test checks a fresh plan.
    """
    found: list[tuple[int, str]] = []
    found.extend(_run_violations(order, max_run))
    found.extend(_spacing_violations(order, min_gap_after_intro))
    found.extend(_pinning_violations(order))
    found.sort(key=lambda entry: entry[0])
    return [message for _, message in found]


def _chains(plan: Sequence[PlannedStep]) -> dict[str, list[int]]:
    """Each item's steps in the order they will be shown, relative to each other.

    The caller's order is kept, except that an intro is pulled to the front of its own group. An item
    cannot be retested before it has been introduced, so honouring a chain that says otherwise would
    only produce a session that is wrong in a way the caller cannot see.
    """
    chains: dict[str, list[int]] = {}
    for i, step in enumerate(plan):
        chains.setdefault(step.item_key, []).append(i)
    for indices in chains.values():
        intro = next((rank for rank, i in enumerate(indices) if plan[i].kind is StepKind.intro_item), None)
        if intro:
            indices.insert(0, indices.pop(intro))
    return chains


def _roles(plan: Sequence[PlannedStep], chains: Mapping[str, Sequence[int]]) -> list[int]:
    """Rank every step inside its item: the intro, its immediate check, then the gated retests."""
    roles = [_UNGATED] * len(plan)
    for indices in chains.values():
        if not indices or plan[indices[0]].kind is not StepKind.intro_item:
            continue
        for rank, i in enumerate(indices):
            roles[i] = rank
    return roles


def _intro_deadlines(
    plan: Sequence[PlannedStep],
    roles: Sequence[int],
    chains: Mapping[str, Sequence[int]],
    *,
    free_slots: int,
    min_gap_after_intro: int,
) -> dict[str, int]:
    """The last position at which each item's intro can still leave room for its own retests.

    Without this the greedy happily introduces an item three steps from the end and then has nowhere
    legal to put the retest that justified introducing it. A retest pinned to the wrap-up can use the
    whole session; one that is not has to fit inside the free block, which ends earlier.

    The reserve grows item by item, since every new item competes for the same tail slots: an intro
    has to leave room for its own check and retests *and* for the whole chain of every item already
    spoken for. Without that stagger the last two items get introduced back to back at the edge of
    the session, and the reviews they displaced pile up behind them in one long run.
    """
    horizons = {False: free_slots - 1, True: len(plan) - 1}
    taken = {False: 0, True: 0}
    deadlines: dict[str, int] = {}
    for key, indices in chains.items():
        gated = [i for i in indices if roles[i] >= _GATED]
        if not gated:
            continue
        # An item with any unpinned retest is bound by the free block; its wrap-up then follows anyway.
        pinned_only = all(plan[i].pinned_last for i in gated)
        taken[pinned_only] += sum(1 for i in indices if plan[i].pinned_last == pinned_only)
        deadlines[key] = horizons[pinned_only] - min_gap_after_intro - taken[pinned_only]
    return deadlines


def _arrange(
    plan: Sequence[PlannedStep],
    roles: Sequence[int],
    chains: Mapping[str, Sequence[int]],
    chunk: Sequence[int],
    out: list[int],
    intro_at: dict[str, int],
    deadlines: Mapping[str, int],
    rng: random.Random,
    *,
    max_run: int,
    min_gap_after_intro: int,
) -> None:
    """Append ``chunk`` to ``out`` one slot at a time. ``out`` and ``intro_at`` carry across chunks,
    so a wrap-up retest is still spaced against an intro that landed in the free block."""
    here = set(chunk)
    pending: dict[str, deque[int]] = {}
    for i in chunk:
        key = plan[i].item_key
        if key not in pending:
            pending[key] = deque(j for j in chains[key] if j in here)
    remaining: Counter[StepKind] = Counter(plan[i].kind for i in chunk)

    while pending:
        pos = len(out)
        tail_kind, tail_run = _tail_run(plan, out)
        heads = [queue[0] for queue in pending.values()]
        pick = _forced_check(plan, roles, out, pending)
        if pick is None:
            pick = _due(plan, roles, deadlines, heads, pos)
        if pick is None:
            ready = [i for i in heads if _gap_ok(plan, roles, intro_at, i, pos, min_gap_after_intro)]
            spaced = [i for i in ready if plan[i].kind is not tail_kind or tail_run < max_run]
            room = [i for i in spaced if _leaves_room(plan[i].kind, remaining, max_run)]
            pick = _pick(plan, room or spaced or ready or heads, remaining, rng)

        key = plan[pick].item_key
        queue = pending[key]
        queue.popleft()
        if not queue:
            del pending[key]
        remaining[plan[pick].kind] -= 1
        if roles[pick] == _INTRO:
            intro_at[key] = pos
        out.append(pick)


def _forced_check(
    plan: Sequence[PlannedStep],
    roles: Sequence[int],
    out: Sequence[int],
    pending: Mapping[str, deque[int]],
) -> int | None:
    """The check belonging to the item just introduced. "Immediate" is taken literally: it goes in
    the very next slot, even if that costs a run, because a delayed first recall is a different
    exercise from the one the plan asks for."""
    if not out or roles[out[-1]] != _INTRO:
        return None
    queue = pending.get(plan[out[-1]].item_key)
    if queue is None:
        return None
    return queue[0] if roles[queue[0]] == _CHECK else None


def _gap_ok(
    plan: Sequence[PlannedStep],
    roles: Sequence[int],
    intro_at: Mapping[str, int],
    i: int,
    pos: int,
    min_gap_after_intro: int,
) -> bool:
    if roles[i] < _GATED:
        return True
    intro = intro_at.get(plan[i].item_key)
    # No intro in this session means nothing to expand away from: the item was introduced days ago.
    return intro is None or pos - intro >= min_gap_after_intro


def _deadline(
    plan: Sequence[PlannedStep],
    roles: Sequence[int],
    deadlines: Mapping[str, int],
    i: int,
) -> int | None:
    return deadlines.get(plan[i].item_key) if roles[i] == _INTRO else None


def _due(
    plan: Sequence[PlannedStep],
    roles: Sequence[int],
    deadlines: Mapping[str, int],
    heads: Sequence[int],
    pos: int,
) -> int | None:
    """An intro that has run out of room goes now, ahead of the draw and ahead of the run limit.

    This is the only thing enforcing a deadline, so it looks at every head rather than at the ones
    the other filters left: an intro that has already slipped past its deadline needs placing more
    urgently than one that has not, not less.
    """
    overdue = [i for i in heads if (limit := _deadline(plan, roles, deadlines, i)) is not None and pos >= limit]
    return min(overdue, key=lambda i: (deadlines[plan[i].item_key], i)) if overdue else None


def _tail_run(plan: Sequence[PlannedStep], out: Sequence[int]) -> tuple[StepKind | None, int]:
    """The kind currently at the end of the order and how many of it are stacked there."""
    if not out:
        return None, 0
    kind = plan[out[-1]].kind
    length = 0
    for i in reversed(out):
        if plan[i].kind is not kind:
            break
        length += 1
    return kind, length


def _leaves_room(kind: StepKind, remaining: Mapping[StepKind, int], max_run: int) -> bool:
    """Whether the rest of the chunk can still avoid an over-long run once ``kind`` takes this slot.

    ``n`` steps of one kind need ``ceil(n / max_run) - 1`` steps of any other kind wedged between
    them. Spending a slot on a minority kind uses up one of those wedges, so without this check the
    draw cheerfully burns its separators early and leaves the dominant kind piled up at the end --
    the exact failure a warm-up of twelve recognition reviews provokes.
    """
    left = sum(remaining.values()) - 1
    for other, count in remaining.items():
        n = count - 1 if other is kind else count
        if n > 0 and left - n < -(-n // max_run) - 1:
            return False
    return True


def _pick(
    plan: Sequence[PlannedStep],
    candidates: Sequence[int],
    remaining: Mapping[StepKind, int],
    rng: random.Random,
) -> int:
    """Draw a kind in proportion to how much of it is left, then a step of that kind.

    Weighting per kind rather than per candidate is what keeps the mix even: a warm-up of twelve
    separate review cards would otherwise outvote four intros twelve to one and bunch the new items
    at the end of the session.
    """
    by_kind: dict[StepKind, list[int]] = {}
    for i in candidates:
        by_kind.setdefault(plan[i].kind, []).append(i)
    kinds = list(by_kind)
    kind = rng.choices(kinds, weights=[remaining[k] for k in kinds], k=1)[0]
    return rng.choice(by_kind[kind])


def _group(plan: Sequence[PlannedStep]) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for i, step in enumerate(plan):
        groups.setdefault(step.item_key, []).append(i)
    return groups


def _run_violations(order: Sequence[PlannedStep], max_run: int) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    start = 0
    for i in range(1, len(order) + 1):
        if i < len(order) and order[i].kind is order[start].kind:
            continue
        if i - start > max_run:
            found.append((start, f"index {start}: run of {i - start} {order[start].kind} steps (max {max_run})"))
        start = i
    return found


def _spacing_violations(order: Sequence[PlannedStep], min_gap_after_intro: int) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for key, at in _group(order).items():
        intro = next((i for i in at if order[i].kind is StepKind.intro_item), None)
        if intro is None:
            continue
        for i in at:
            if i < intro:
                found.append((i, f"index {i}: {order[i].kind} for {key} precedes its intro at index {intro}"))
        # The first step after the intro is the immediate check and owes it no distance.
        for i in [i for i in at if i > intro][1:]:
            if i - intro < min_gap_after_intro:
                found.append(
                    (
                        i,
                        f"index {i}: {order[i].kind} retests {key} only {i - intro} steps "
                        f"after its intro (need {min_gap_after_intro})",
                    )
                )
    return found


def _pinning_violations(order: Sequence[PlannedStep]) -> list[tuple[int, str]]:
    last_free = max((i for i, step in enumerate(order) if not step.pinned_last), default=-1)
    return [
        (i, f"index {i}: wrap-up {step.kind} for {step.item_key} is followed by regular steps")
        for i, step in enumerate(order)
        if step.pinned_last and i < last_free
    ]
