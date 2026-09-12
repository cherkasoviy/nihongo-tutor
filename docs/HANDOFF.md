# Hand-off: Phase 1 complete, continue with Phase 2

This note lets a fresh Claude Code session pick up where the Phase 1 work stopped. Read it together
with `docs/PLAN.md` and `README.md`.

## State of the repository

- Branch `claude/phase-1-kana` holds Phase 1; it branches off `main` at the Phase 0 hand-off commit.
- Phase 1 scope from the roadmap is implemented except audio: FSRS wrapper, adaptive planner,
  interleaving, forgiving streak, grading, the session engine shared by bot and Mini App, the
  208-syllable kana curriculum with an idempotent importer, reminders, `/stats`, and the Mini App's
  session runner, kana grid, progress and settings screens.
- Beyond the roadmap: a learner-settable pace, placement for syllables already known, extra practice
  sittings, and free recall for cards that reach review state. See
  [Deviations from PLAN.md](#deviations-from-planmd).
- Verification at hand-off: ruff / black / mypy --strict clean, 857 tests passing against Postgres 16,
  `alembic upgrade → downgrade → upgrade → check` clean, `npm run build` clean, API boots and
  `/healthz` answers. Docker image builds were not exercised.
- Nothing has been deployed. No GCP project or domain exist yet.

## Deliberately not done in Phase 1

- **Audio (TTS), and therefore the listening and shadowing steps.** The owner deferred TTS/STT. The
  schema keeps the hooks (`kana.audio_asset_id`, `audio_assets`, the planner's `listening` and
  `output` section budgets), and `StepKind` already carries `listen_choose` / `shadow` / `speak`, so
  adding audio is filling slots rather than reshaping anything. `session_service` currently emits no
  steps for those sections.
- **KanjiVG stroke order is not vendored.** `make fetch-kanjivg` downloads it; the Mini App renders
  the grid fine without it.
- **Typed answers.** `domain/grading.py` implements the plan's normalisation (jaconv, katakana folded
  to hiragana) and the rapidfuzz typo threshold, and nothing calls it: the bot has no text-message
  handler for answers. Free recall covers the same pedagogical ground for kana without asking the
  learner to type Japanese on a phone, so this waits for Phase 2's cloze steps, where typing is the
  natural input.
- **`ai_usage_ledger` and `audio_assets`** exist and are never written; they belong to Phases 2-3.

## Gaps closed after the first local run

Running it for real turned up several things that were built but unreachable. All of these now work
end to end:

- **The daily reminder.** The cron was complete and tested and could never fire: `reminder_time` was
  NULL for everyone and onboarding never asked. Joining now asks, `/reminder` changes it, and the
  Mini App has a settings screen. The Mini App also reports the browser's IANA zone on login, which
  the backend adopts only while the learner is still on the default.
- **An extra sitting the same day.** Finishing the lesson used to end with "come back tomorrow".
  `learning_sessions.kind` now separates the planned lesson from `practice` sittings: no new items,
  no second chance at the streak, and drills the scheduler did not ask for are logged
  `intra_session` so cramming cannot push a real review out. `/review` is the plan's five-minute
  queue rather than an alias for `/today`.
- **Free recall.** Recognition cards that reach `review` state graduate from a four-option grid to
  the plan's three-button self-grading, with Hard inferred from the reveal delay.
- **The stop button**, which was wired to a handler but never rendered.

## Implementation decisions not in PLAN.md

How things were built, where the plan was silent. Product-level departures are in
[Deviations from PLAN.md](#deviations-from-planmd) below.

- `cards.step` was added: FSRS 6 keeps the learning-step index on the card, and without persisting it
  every restart would send a card back to its first learning step.
- FSRS 6 has no `New` state. `CardState.new` is ours alone and collapses to `Learning` on the way
  into the library; only `domain/srs.py` knows this.
- Intra-session retests log with `intra_session=True` **and do not reschedule the card**
  (`card_service.record_review(apply_state=False)`). The plan only says the optimizer excludes them;
  letting them move the schedule would push the next real review out by weeks on the strength of an
  echo.
- Item slugs are `kana:{script}:{row}:{romaji_key}`. `romaji_key` alone is not unique — modern
  Japanese merged ぢ into じ and づ into ず, so each script has two syllables keyed `ji` and two `zu`.
- The reminder cron compares timezones in Python (`zoneinfo`) rather than SQL `AT TIME ZONE`. The
  candidate set is tiny and the plan's own verification asks for the cron to be parametrized over
  timezones, which is trivial against a pure function.
- `.gitignore`'s blanket `data/` was narrowed: it was silently excluding `backend/data/seed/`.

## Resolved: the kana pace (was the open question here)

The plan's Phase 1 verification asked for "a simulated 30-day learner completing kana within
12-20 min/day", and its own numbers could not produce it: `base = 5` with a `min(new + 1, 10)` nudge
caps a kana day at **six** new syllables, which is 3-6 minutes of content and roughly 35 days for the
syllabary.

Answered by measurement rather than argument. A 30-day sweep across bases 5/8/10/15/20 and three
accuracy profiles:

| base | acc | session min/mean/max | peak backlog | taught /208 | hiragana | all kana |
|---|---|---|---|---|---|---|
| 5 | 1.00 | 2.7 / 4.5 / 5.3 | 0.36 | 177 | day 18 | never |
| 5 | 0.70 | 2.4 / 3.8 / 5.9 | 0.72 | **94** | never | never |
| **10** | 1.00 | 0.4 / 5.5 / 8.4 | 0.52 | 208 | day 11 | day 21 |
| 10 | 0.85 | 1.9 / 6.7 / 10.4 | 0.85 | 208 | day 11 | day 21 |
| 20 | 0.85 | 0.9 / 7.0 / 16.4 | 1.19 | 208 | day 6 | day 14 |

At five, a learner recalling 70% of what they see never finishes hiragana in a month. **The kana
default is now 10 and the learner can choose up to `MAX_NEW_PER_DAY`** (`users.daily_new_items_target`,
`/pace`, or the Mini App). `docs/PLAN.md` has been amended to match, with the reasoning, so the plan
and the code no longer disagree on paper.

The claim that "the code implements the plan verbatim" is therefore no longer true, and the
deviations are listed below.

## Deviations from PLAN.md

Changes to *what the product does*, as opposed to how it is built. Every one is also written into
`docs/PLAN.md`, so the two documents no longer disagree.

- **Kana pace.** `base = 5` → `10`, and the high-retention ceiling `10` → `MAX_NEW_PER_DAY` (20).
  Left at 10 the ceiling would have silently disabled the bump once the base reached 10. The pace is
  a per-learner setting; the backlog gate, not a low default, is what keeps it safe.
- **Placement.** Not in the plan at all. A learner who already reads some kana can mark syllables
  known, by gojūon group or individually. A claim is *seeded, not skipped*: written as the state the
  scheduler produces for two correct answers with the due date pulled into a spread window, so every
  claim is verified within a couple of weeks instead of trusted. Two people share this curriculum
  from opposite ends — a complete beginner and someone who reads the gojūon — and without this the
  second one spends three weeks on a formality.
- **A day with nothing to do counts toward the streak.** The plan does not consider the case. Once
  everything available has been taught and nothing is due the session is empty, and turning up to
  that is not failing it. Reachable well before Phase 2: at the current pace the syllabary finishes
  around day 21.
- **Extra sittings.** `learning_sessions.kind` separates the planned lesson from `practice`. The
  plan has no notion of a second sitting; finishing the day used to mean being told to come back
  tomorrow. Practice introduces no new items and cannot earn the streak.
- **Free recall.** Recognition cards in `review` state use the plan's three-button self-grading
  instead of a choice grid. The plan lists both mechanisms but does not say when each applies.
- **One planned lesson per learner-day** is enforced by a partial unique index, which the plan does
  not specify. A bug in the stop handler issued the day's new items twice and nothing objected.

## Local workflow reminders

- `make db-up`, `make migrate`, `make seed`, `make dev`, `make worker`, `make check`.
- `ADMIN_TG_IDS` must hold your numeric Telegram id (ask @userinfobot) or nobody can register.
- Model changes need `make revision m="..."` and a review of the generated file; CI runs
  `alembic check` and fails on drift.
- All learner-facing strings live in `app/bot/texts_ru.py`; the Mini App still has its Russian strings
  inline (worth centralising when the UI grows).

## Phase 2 entry point

> Read docs/HANDOFF.md, docs/PLAN.md and README.md. Phase 1 is complete. Create branch
> `claude/phase-2-vocab` from `main` and implement Phase 2 (vocab/grammar + listening) as the roadmap
> describes: the content pipeline end to end, `sentence_tokens` and i+1 selection, cloze and listening
> steps, the Mini App session runner with ruby furigana, pitch marks, normal/slow audio, and the admin
> review queue. Write the Phase 2 tests listed in the plan's Verification section.

Phase 2 needs from the owner: an Anthropic API key. The kana-pace question is settled (see above).
Google TTS and OpenAI STT keys arrive with the audio and speaking work.
