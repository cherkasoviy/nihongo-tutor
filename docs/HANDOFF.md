# Hand-off: Phase 1 complete, continue with Phase 2

This note lets a fresh Claude Code session pick up where the Phase 1 work stopped. Read it together
with `docs/PLAN.md` and `README.md`.

## State of the repository

- Branch `claude/phase-1-kana` holds Phase 1; it branches off `main` at the Phase 0 hand-off commit.
- Phase 1 scope from the roadmap is implemented except audio: FSRS wrapper, adaptive planner,
  interleaving, forgiving streak, grading, the session engine shared by bot and Mini App, the
  208-syllable kana curriculum with an idempotent importer, reminders, `/stats`, and the Mini App's
  session runner, kana grid and progress screens.
- Verification at hand-off: ruff / black / mypy --strict clean, 714 tests passing against Postgres 16,
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
- **`/review` reuses today's session.** A dedicated five-minute queue needs Phase 2's much larger due
  pool to be worth building.

## Decisions taken during Phase 1 that are not in PLAN.md

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

## Open question for the owner (worth answering before Phase 2)

The plan's Phase 1 verification asks for "a simulated 30-day learner completing kana within
12-20 min/day". The plan's own numbers cannot produce that, and the simulation in
`tests/integration/test_learner_simulation.py` documents it:

- The kana stage fixes `base = 5` new items/day, and the only upward nudge is `min(new + 1, 10)` — an
  increment on the base, so **six** is the real ceiling, not ten.
- Six syllables at four steps each plus the day's reviews is 25-45 steps ≈ **3-6 minutes** at the
  planner's own 8 s/step, not 12-20.
- 208 syllables at six a day is **~35 days**, so nobody finishes all kana inside 30. Hiragana (104),
  which is what the bootcamp gates on, finishes around day 18.

Measured over 30 simulated days: a perfect learner reaches 177/208 syllables, an 85%-accuracy learner
150/208, a 70% learner 102/208 — the adaptive throttle works, the sessions are just short. Raising the
kana base (say to 12-15/day) would land sessions in the intended band and finish kana inside a month.
That is a product decision, so the code implements the plan verbatim and the test asserts what is
actually true.

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

Phase 2 needs from the owner: an Anthropic API key, and a decision on the kana-pace question above.
Google TTS and OpenAI STT keys arrive with the audio and speaking work.
