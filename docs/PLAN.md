# Nihongo Tutor: Japanese learning platform (Telegram bot + Mini App)

## Context

Igor wants a pet project to help his girlfriend learn Japanese. It lives in this repo. The app must follow contemporary second-language-acquisition research rather than generic flashcard gamification.

### Decisions made with the user

| Topic | Decision |
|---|---|
| Learner level | Between absolute beginner and JLPT N5 |
| Goals | Everyday conversation/speaking; understanding anime/drama (listening). Not exam-driven |
| L1 / UI language | Russian for all glosses, explanations and UI |
| Daily time | One 15-20 min session per day |
| Platform | Telegram bot (habit loop, reviews, voice) + Telegram Mini App (rich UI: furigana, kana grid, stats, dialogues) |
| Stack | Python 3.12 + FastAPI + SQLAlchemy 2 async + Alembic + Postgres 16; aiogram 3 (webhook mode); arq + Redis for jobs; React 18 + TS + Vite Mini App; uv; docker-compose + Caddy |
| Hosting | **GCP `e2-medium`** (2 vCPU, 4 GB, 30 GB disk, `europe-west3`/`europe-north1`), Ubuntu 24.04 + Docker. Single vendor with the TTS API; TTS auth via the VM's attached service account (no JSON key on disk). Reevaluate after the $300 credit; nothing in the plan is GCP-specific, so a move to Hetzner is a `pg_dump` + audio volume copy |
| AI | Claude API (content generation, roleplay, corrections); Google Cloud TTS; OpenAI transcription for STT |
| Users | Multi-user from day one, invite-only, per-user daily AI budget, admin role |
| Curriculum | App is the main path: kana bootcamp, then frequency-based core vocab + N5 grammar in usage order, inside conversational scenarios. Real kanji with furigana toggle from day one, **no romaji rendered anywhere** |

### Prerequisites before deployment (user's side)

1. Telegram bot token from BotFather; enable the Mini App (`/newapp`) and set the domain.
2. Keys: Anthropic API key, OpenAI API key (STT), Google Cloud service account with Text-to-Speech enabled.
3. GCP project with billing (credit) enabled, `e2-medium` VM with a static IP, a service account (Text-to-Speech role) attached to the VM, and a domain pointing at it (Caddy needs a public hostname for TLS + webhook). Secrets go only in `.env` on the server, never in the repo.

### Glossary

- **Alembic**: migration tool for SQLAlchemy. Each schema change is a versioned script applied with `alembic upgrade head`, so the production database evolves across phases without losing review history.
- **FSRS** (Free Spaced Repetition Scheduler): the open-source scheduler that replaced SM-2 in Anki. Models each card with difficulty and memory stability, predicts recall probability per day, and schedules the next review when it would fall to the target (0.90). Needs 20-30% fewer reviews than SM-2 for the same retention and can be fitted to a learner's own review log.
- **Cloze**: fill-in-the-blank drill. The target word is removed from a sentence (毎朝コーヒーを ＿＿ ます, cue "пить"), the learner produces のみ. Active recall in context rather than card recognition.

## Pedagogy to feature mapping

| Research principle | What the app does |
|---|---|
| Spaced repetition with a modern scheduler | **FSRS** (py-fsrs) per card, not SM-2. Desired retention 0.90 default, tunable per user. Optimizer re-run monthly per user once ≥1000 review logs exist |
| Retrieval practice / testing effect | Every exposure after the first is a recall test, never a re-read |
| Production and recognition are different memories | Separate **recognition**, **production** and **listening** cards per vocab item. Production (cloze) is required within the first session before the recognition card gets its first FSRS grade; production card comes due the next day, listening in two days |
| Context, not isolated words | A word is never shown alone: always inside a sentence built from known words. Cloze of the target is the default drill |
| Comprehensible input at i+1 | Sentence selection and generation are constrained to the learner's known-lemma set plus exactly one new item, verified by a morphological analyzer |
| Interleaving | A session shuffles vocab, grammar, listening and output steps under constraints (no 3 same kinds in a row, retests ≥5 steps after intro) |
| Within-session expanding spacing | New item: intro, immediate check, cloze after ≥5 steps, retest at wrap-up. Only the wrap-up grade writes an FSRS review; massed intra-session reviews are excluded from the optimizer |
| Desirable difficulties | Honest grading, listening plays native speed first, slow replay on demand |
| Output with corrective feedback (recasts) | Speaking and roleplay answers get a natural recast plus one Russian sentence about what differed; never a red-pen lecture or phoneme claims |
| Kana first, kanji early with support | Kana bootcamp gates everything; kanji appear from the first vocab item with furigana on by default, auto-off per word after N successful recalls |
| Pitch accent (light) | Pitch pattern stored per lemma (Kanjium data) and shown as a mark; no drills in v1 |
| Habit formation, no punishing gamification | Reminder at a fixed local time, a **forgiving streak** (one automatic freeze per week), session counts as done at ≥60% or ≥12 min, no leaderboards or hearts |
| Avoid translation-only drills | Translation is a gloss for meaning; tasks are cloze, listen-and-choose, respond in Japanese |

## Technology choices

| Concern | Choice | Why |
|---|---|---|
| Bot transport | aiogram 3 in webhook mode served by the FastAPI app | One process, one public URL; bot and Mini App share the session engine |
| Jobs | arq + Redis worker; reminder tick is a cron every minute | Survives restarts, fans out TTS/STT/batch polling; per-user scheduled jobs would drift |
| SRS | `fsrs` (py-fsrs) `Scheduler/Card/Rating/ReviewLog`; `fsrs[optimizer]` only in an offline script | Optimizer pulls torch; run monthly offline |
| Morphology | `sudachipy` + `sudachidict_core`, `jaconv` for kana normalization; pitch from Kanjium accent file | Lemma + reading per token for validation and i+1 queries |
| LLM | `anthropic` 1.x. `MODEL_STRONG=claude-opus-5`, `MODEL_FAST=claude-opus-5` with `output_config.effort="low"` for chat turns. `claude-sonnet-5` is an opt-in config value for `MODEL_FAST` after the user evaluates quality | Japanese naturalness and Russian explanation quality are where cheaper models slip; Batch API keeps offline generation cheap regardless |
| TTS | Google Cloud Text-to-Speech, `ja-JP-Neural2-B/C/D` (Chirp3-HD optional for dialogues), SSML `prosody rate` for the slow variant, native `OGG_OPUS` output | 1M chars/month free tier, Telegram-ready format, word timepoints. Azure Neural is a close second; OpenAI TTS lacks SSML; ElevenLabs is 10-20x the price |
| STT | OpenAI `gpt-4o-transcribe` (`whisper-1` fallback), `language="ja"`, `prompt=<target sentence>` | Accepts OGG/Opus directly; prompt biases toward the expected phrase. Reject clips <1 s (hallucination) and >20 s |
| Mini App | React 18, TS, Vite, `@telegram-apps/sdk-react`, TanStack Query, zustand, CSS modules; API client generated with openapi-typescript | |
| Deploy | docker-compose: caddy (TLS, static Mini App, `/api`, `/tg/webhook`, `/audio`), api, worker, postgres, redis; nightly `pg_dump` | |

## Repository layout

```
nihongo-tutor/
├── backend/
│   ├── pyproject.toml, alembic.ini, alembic/versions/
│   ├── app/
│   │   ├── main.py            # FastAPI factory; mounts /api and /tg/webhook; lifespan starts aiogram Dispatcher
│   │   ├── config.py          # pydantic-settings: DB_URL, REDIS_URL, BOT_TOKEN, ANTHROPIC_API_KEY, OPENAI_API_KEY,
│   │   │                      #   GOOGLE_APPLICATION_CREDENTIALS, MINIAPP_URL, ADMIN_TG_IDS, DEFAULT_DAILY_BUDGET_USD, MODEL_STRONG, MODEL_FAST
│   │   ├── db/                # base.py (engine, session dep), models/{users,content,learning,ai,audio}.py
│   │   ├── domain/            # pure logic, no I/O: srs.py, session_planner.py, interleave.py, i_plus_one.py, grading.py, furigana.py, streak.py
│   │   ├── services/          # session_service.py, plan_service.py, content_service.py, invite_service.py, stats_service.py, admin_service.py
│   │   ├── ai/                # client.py (budget check, ledger, caching, refusal handling), budget.py, prompts/*.md (versioned),
│   │   │                      #   schemas.py (structured outputs), tasks/{generate_sentences,gloss_fill,grammar_explain,roleplay,speak_feedback}.py, validate.py
│   │   ├── speech/            # tts.py (Provider protocol + Google impl, asset cache), stt.py (OpenAI impl)
│   │   ├── bot/               # dispatcher.py, handlers/{start,today,review,voice,settings,stats,admin}.py, keyboards.py, render.py, texts_ru.py
│   │   ├── api/               # deps.py (initData -> user, admin guard), routers/{auth,session,content,stats,settings,admin,audio}.py
│   │   ├── workers/           # worker.py (arq settings), reminders.py, audio_jobs.py, generation_jobs.py, maintenance.py
│   │   └── content_pipeline/  # typer CLI: import_kana, import_jmdict, import_freq, build_vocab, gen_sentences, gen_dialogues, gen_audio
│   ├── data/seed/             # kana.json, grammar_n5.yaml, scenarios.yaml, kanjium accents, frequency lists (or download script)
│   └── tests/                 # unit/, integration/ (testcontainers postgres), bot/ (aiogram MockedBot)
├── miniapp/src/               # tg/ (sdk init, auth), api/, features/{session,jp,stats,settings,admin}/, store/
├── infra/                     # docker-compose.yml, Caddyfile, backend.Dockerfile, miniapp.Dockerfile, scripts/{backup,deploy}.sh, .env.example
└── Makefile                   # dev / test / migrate / seed / gen-content
```

Backend deps: fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, alembic, pydantic-settings, aiogram, arq, redis, anthropic, openai, google-cloud-texttospeech, fsrs, sudachipy, sudachidict-core, jaconv, rapidfuzz, structlog, typer. Dev: pytest, pytest-asyncio, testcontainers[postgres], factory-boy, respx, freezegun, ruff, black (120), mypy.

## Data model (Postgres, UUIDv7 keys, timestamptz, JSONB for payloads)

**Users / access**
- `users`: tg_user_id (unique), role (learner|admin), status, timezone (IANA), reminder_time, daily_minutes_target (17), furigana_mode, daily_budget_usd, desired_retention (0.90), fsrs_params jsonb null, invited_by, onboarded_at, last_active_at.
- `invites`: code (unique), created_by, max_uses, uses, expires_at, revoked. `invite_redemptions`: invite_id, user_id.

**Content (shared)**
- `items`: polymorphic hub so cards have one FK: type (kana|vocab|grammar|sentence|dialogue), ref_id, slug, curriculum_order, stage (kana_hira|kana_kata|core), active.
- `kana`: char, script, romaji_key (internal only, for typed input), cyrillic (Polivanov), row, kind, group_order, audio_asset_id.
- `vocab_lemmas`: lemma, reading (hiragana), pos, jlpt_level, freq_rank, pitch_pattern, jmdict_seq, gloss_ru, gloss_ru_short, gloss_source (jmdict_rus|warodai|claude|human), gloss_review_status, notes_ru, kanji_forms[], audio_asset_id.
- `grammar_points`: key, title_ru, pattern, explanation_ru, order_index, prereq_keys[], review_status.
- `sentences`: text, reading, furigana jsonb (segments), translation_ru, target_vocab_id, target_grammar_id, max_freq_rank, lemma_ids[], grammar_keys[], register (polite|casual), scenario_id, source (claude|tatoeba|human), validation jsonb, review_status (auto_ok|needs_review|approved|rejected), audio_normal_id, audio_slow_id. Unique on normalized text.
- `sentence_tokens`: sentence_id, idx, surface, lemma_id, pos, reading (from Sudachi; drives i+1 queries).
- `scenarios`: key (konbini, self_intro, ordering_ramen...), title_ru, required_vocab_ids[], required_grammar_keys[], roleplay_system_prompt. `dialogues`: scenario_id, lines jsonb, review_status.

**Learning (per user)**
- `cards`: user_id, item_id, direction (recognition|production|listening), FSRS state (state, stability, difficulty, due, last_review, reps, lapses, elapsed_days, scheduled_days), suspended. Unique (user_id, item_id, direction); index (user_id, due).
- `review_logs` (append-only, optimizer input): card_id, rating 1-4, review_at, elapsed_days, scheduled_days, state_before, response_ms, auto_graded, intra_session flag, answer_payload.
- `learning_sessions`: user_id, local_date, started_at, finished_at, client (bot|miniapp), planned/completed steps, outcome.
- `session_steps`: session_id, idx, kind (review_recog|review_prod|intro_item|cloze|listen_choose|shadow|speak|roleplay|wrapup), card_id, sentence_id, payload, status (pending|shown|answered|skipped), result, tg_message_id.
- `daily_plans` PK (user_id, local_date): new_items_target, due_count, retention_7d, backlog_ratio, plan jsonb.
- `user_known_lemmas`: user_id, lemma_id, strength (seen|learning|known); refreshed at session end from recognition-card stability.
- `streaks`: current, longest, last_active_date, freezes_available, freeze_used_dates[].

**AI / audio**
- `ai_usage_ledger`: user_id (null = shared generation), task, provider, model, input/cache_read/cache_write/output tokens, audio_seconds, chars, cost_usd, request_id, session_id. `ai_budgets_daily`: (user_id, local_date), spent_usd, limit_usd.
- `audio_assets`: hash = sha256(provider|voice|rate|ssml_version|text) unique, ogg_path, mp3_path, duration_ms, timepoints, tg_file_id (cached after first Telegram upload).
- `generation_batches`: anthropic_batch_id, task, prompt_version, counts, cost. `content_reviews`: admin QA trail.

## Session engine (`domain/session_planner.py`, `interleave.py`, `i_plus_one.py`, `srs.py`)

**Adaptive new items per day**
```
base = 5 kana/day in kana stage; 4 vocab + 1 grammar every 2nd day in core stage
backlog_ratio = due_count / (0.35 * T / avg_review_s)
if backlog_ratio > 1.5: new = 0
elif backlog_ratio > 1.0: new = max(1, base // 3)
else: new = base
if retention_7d < 0.80: new = max(1, new - 2)
if retention_7d > 0.92 and last 3 sessions finished under T: new = min(new + 1, 10)
if missed >= 3 days: new = min(new, 2) for the first session back
```
Grammar points unlock only when prerequisites have a review-state card and ≥80% of the point's example lemmas are known.

**Composition of one session (T = 17 min default)**: warm-up reviews ~35% (retrievability-sorted, rendered in sentence context or as cloze) → new items ~25% (intro with audio, furigana, gloss, pitch mark; immediate check; delayed cloze) → listening ~15% (2-4 audio-first steps, four distractors drawn algorithmically from known items) → one output task ~15% (kana stage: shadowing; core stage: alternate `speak` and `roleplay`, chat-only) → wrap-up ~10% (retest new items, summary, tomorrow preview). Blueprint stored in `daily_plans.plan`; steps materialized lazily so sentence choice reflects the latest known set. Seeded RNG (user_id + date) so reopening gives the same order.

**i+1 selection**: SQL over `sentence_tokens` requiring every non-target lemma ∈ known set and `grammar_keys` ⊆ known grammar; fallback chain: allow one `learning`-strength lemma → lowest `max_freq_rank` pre-generated sentence → enqueue a personal sentence from Claude (if budget remains). Seed content is generated at four vocab ceilings (rank ≤100/300/600/1000) so the third fallback is rare.

**Grading → FSRS rating**: auto-graded steps: correct and fast → Good, correct after hint/retry or slow → Hard, wrong → Again, optional "Легко" → Easy. Self-graded recognition in chat: three buttons (Не помню / Помню / Легко), Hard inferred from reveal time >8 s. Typed kana answers normalized with jaconv; rapidfuzz ratio ≥0.9 counts as a typo (Hard).

## Bot UX

- Commands: `/start <code>` (invite + onboarding: timezone from Mini App, reminder time, furigana default), `/today`, `/review` (5-min reviews only), `/stats`, `/settings`, `/pause`, `/resume`, `/help`, `/admin`.
- One live message per step edited in place; answered steps show result + explanation; old keyboards removed. Callback data via aiogram `CallbackData` factories, idempotent on non-pending steps.
- Furigana in chat: no ruby markup in Telegram, so `食(た)べます` plus the full reading inside a `<tg-spoiler>` acts as the toggle. Full ruby experience lives in the Mini App.
- Audio as `voice` (OGG/Opus) with a "🐢 медленно" button; `tg_file_id` cached.
- Voice flow: bot sends cue + model audio → learner sends voice → reject <1 s or >20 s → arq STT job → `grading.speak_score` (hiragana-normalized similarity + lemma overlap: ≥0.9 Good, 0.7-0.9 Hard, <0.7 Again) → for Hard/Again a `speak_feedback` call returns recast + one Russian note; one retry offered.
- Roleplay: up to 6 exchanges in a scenario using known vocab; Claude returns `{reply_ja, reply_furigana, reply_ru, user_recast_ja, correction_note_ru, should_end}`; bot sends text + TTS voice.
- Mini App vs chat: kana stage runs in chat; core-stage reviews, intros, cloze, dialogue reader, stats, settings, admin run in the Mini App (`t.me/<bot>/app?startapp=session`); speaking steps always hand off to chat and back. Both clients use the same `session_service`; `session_steps.status` prevents double answers.
- Reminders: arq cron each minute selects users whose local time (`AT TIME ZONE`) matches `reminder_time`, skips if today's session exists or already reminded (Redis key). Optional second nudge is opt-in.

## AI layer

**Task → model matrix**

| Task | Model | Mode |
|---|---|---|
| Sentence generation, gloss fill, grammar explanations, dialogue scripts (offline) | `claude-opus-5` | Batches API (50% off), structured output, adaptive thinking (default) |
| Personal i+1 sentence (runtime fallback) | `claude-opus-5`, effort medium | online, structured |
| Roleplay turn, output correction, speak feedback | `MODEL_FAST` (default `claude-opus-5`, effort low; `claude-sonnet-5` opt-in) | online, structured |
| Distractors for choice steps | none, algorithmic | |

**SDK rules (anthropic 1.x)**: `AsyncAnthropic()`; structured output via `client.messages.parse(..., output_format=PydanticModel)` or `output_config={"format": {...}}` with `additionalProperties: false`; no assistant prefill, no `temperature`, no `budget_tokens`; server-side fallbacks on by default (`betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`); always check `stop_reason == "refusal"` and fall back to a canned Russian message; batch results keyed by `custom_id`, never by position; `max_tokens` 4096 for runtime JSON turns, 16000 for batch generation.

**Prompt caching**: system prompt ordered stable → volatile: [pedagogy rules + schema description + style guide, `cache_control` 1h] → [user's known lemmas + grammar keys, sorted deterministically, `cache_control` 1h] → messages. Known list changes only at session end, so every turn within a session hits the cache. Verify via `usage.cache_read_input_tokens` in the ledger.

**Validation gate (`ai/validate.py`)** for every generated sentence: Sudachi re-tokenization and furigana comparison (mismatch → `needs_review`, not reject, since the analyzer is fallible on 日/counters/names); every content lemma ∈ allowed set ∪ {target} else reject and re-request naming the leaks; kana-stage regex (hiragana/katakana/punctuation only), no Latin letters anywhere; 4-18 tokens; Cyrillic translation present; dedup on normalized text.

**Budget (`ai/budget.py`)**: pre-call reservation in Redis against `limit - spent`; on exhaustion the step degrades (roleplay → scripted dialogue, speak feedback → similarity-only, personal sentence → pre-generated) with the message "Лимит ИИ на сегодня исчерпан, продолжаем без живого диалога"; never a hard stop. Post-call actual cost from `response.usage` × price table into `ai_usage_ledger` and `ai_budgets_daily`. Shared generation charged to `user_id=NULL` with a global monthly cap and admin alert at 80%.

**Cost estimate**: with Opus 5 everywhere ≈ $0.20/day per active user (≈ $6/month); default per-user cap $0.35/day. Switching `MODEL_FAST` to Sonnet 5 roughly halves it. Shared seed content (~6K sentences, glosses, ~90 grammar explanations, ~40 dialogues) via Batch ≈ $20-40 one-off. TTS within the free tier; STT ≈ $0.35/user/month.

## Content pipeline (`content_pipeline/`, typer CLI, idempotent upserts, `--only-missing` default)

1. **Kana**: hand-authored `kana.json` (basic, dakuten/handakuten, yōon per script) with Polivanov labels and a Russian mnemonic; TTS per syllable and per example word.
2. **Vocabulary**: JMdict (CC BY-SA, Russian glosses where present) + Warodai (JP-RU dictionary) for glosses; BCCWJ short-unit frequency for `freq_rank`; a public N5 list for `jlpt_level`; Kanjium for pitch. `build_vocab` selects N5 ∪ top-1500 frequency content words (~1200-1500 lemmas), orders by weighted rank with N5 first. Gaps and multi-sense words go through a `gloss_fill` batch marked `needs_review`.
3. **Grammar**: `grammar_n5.yaml`, ~90 points hand-ordered by usage (です/は/か → を/に/で/へ → ます forms → adjectives → て-form → たい/ない → counters → casual/plain forms, kept earlier than usual because of the anime goal). `grammar_explain` batch writes Russian explanations with three contrasting examples; admin approves before activation.
4. **Sentences**: per lemma × four vocab ceilings, 5 sentences each, polite first, casual variants from ceiling 600+; 8 per grammar point constrained to earlier grammar; validation gate; Tatoeba JP-RU pairs within top-1000 imported as extra natural variety.
5. **Dialogues**: 2-3 scripted dialogues per scenario, 6-10 lines, also the no-budget roleplay fallback.
6. **Audio**: `ensure_audio(text, voice, rate)` at 1.0 and 0.75, OGG for Telegram and MP3 for the Mini App (iOS WKWebView Ogg support is unreliable), stored at `/data/audio/{hash[:2]}/{hash}.*` behind signed URLs. Distinct voices per dialogue speaker.
7. **Review queue** in the Mini App admin: `needs_review` items with the Sudachi diff highlighted; approve/edit/reject; edits regenerate audio.

## Phased roadmap

| Phase | Scope | Effort |
|---|---|---|
| 0 Skeleton | compose stack, FastAPI + aiogram webhook, Alembic baseline (users, invites, items, cards, review_logs, audio_assets, ai_usage_ledger), `/start <code>`, admin bootstrap from `ADMIN_TG_IDS`, Mini App shell with initData auth → JWT, CI (ruff/black/mypy/pytest, vite build), deploy script | 1-1.5 w |
| 1 Kana bootcamp + SRS | kana seed + audio, FSRS wrapper, session engine v1, chat steps (choice grids, spoilers, voice), Mini App kana grid + stroke order (KanjiVG), reminders, forgiving streak, `/stats` | 2 w |
| 2 Vocab/grammar + listening | content pipeline end to end, `sentence_tokens` + i+1, cloze/listen steps, Mini App session runner with ruby furigana toggle, pitch mark, normal/slow audio, admin review queue | 3-4 w |
| 3 Speaking + roleplay | voice handler + STT job, similarity scoring, speak feedback and roleplay tasks with caching, scripted-dialogue fallback, budget enforcement with degradation, Mini App ↔ chat hand-off | 2 w |
| 4 Admin + optimizer | admin dashboard (users, invites, budgets, spend, review queue, errors), monthly FSRS optimizer script writing `fsrs_params` at ≥1000 logs, retention/backlog charts | 1.5 w |

Later ideas: user-uploaded anime clips → STT → cloze, free-writing diary with recasts, kanji component hints, pitch-accent minimal pairs, Anki export.

## Verification

- **Common**: `domain/*` is pure and unit-tested; integration tests on testcontainers Postgres with `alembic upgrade head`; all providers behind Protocols with fakes (FakeLLM canned JSON, FakeTTS, FakeSTT); `respx` for HTTP regressions; `freezegun` for schedules; no real keys in CI.
- **Phase 0**: initData HMAC tests (valid, expired `auth_date`, tampered hash) using `aiogram.utils.web_app.safe_parse_webapp_init_data`; concurrent invite redemption on `max_uses=1` (`SELECT ... FOR UPDATE`); webhook secret check; compose smoke (`/healthz`, `getWebhookInfo`).
- **Phase 1**: FSRS property tests (Again never raises stability); planner tests (heavy backlog → 0 new, high retention → more, 3 missed days → cap); interleave constraint tests and seeded determinism; streak freeze across DST; reminder cron parametrized over timezones; aiogram `MockedBot` tests for callback idempotency and in-place edits; a simulated 30-day learner completing kana within 12-20 min/day.
- **Phase 2**: golden furigana alignment on ~200 curated sentences; validator tests (vocab leak, romaji ban, kana-only); i+1 SQL fixtures (exactly one unknown, fallback order); pipeline idempotency (run twice, no duplicates, audio hash reuse); structured-output schema strictness; unordered/errored batch results; Vitest for ruby rendering and toggle; Playwright smoke against a mocked API; manual iOS/Android audio and ruby-font check.
- **Phase 3**: recorded OGG fixtures (short clip rejected); similarity score table tests; roleplay parsing incl. `refusal` stop reason and malformed JSON; budget reservation/exhaustion/degradation and day rollover per timezone; nightly key-gated live test asserting `cache_read_input_tokens > 0` on the second call of a session.
- **Phase 4**: admin authz (learner blocked from admin routes); optimizer export excludes intra-session logs; EXPLAIN-checked dashboard indexes; locust run at 50 concurrent users.

## Risks and mitigations

- Telegram voice is OGG/Opus: chosen STT and TTS handle it natively; ffmpeg transcode lives only in the worker image if a provider changes.
- Ruby text is impossible in chat: spoiler fallback, Mini App as the primary reader, kana stage stays in chat where furigana is unnecessary.
- FSRS optimizer needs data: default parameters until ≥1000 logs; massed intra-session reviews excluded; retention configurable 0.85-0.92.
- LLM furigana/vocab errors: validation gate + review queue; only `auto_ok`/`approved` sentences enter rotation; runtime personal sentences validated synchronously and dropped on failure.
- Analyzer vs canonical readings (私 わたし/わたくし): pin readings in `vocab_lemmas.reading` and prefer them.
- Budget runaway: per-user daily cap + global monthly cap + admin Telegram alert at 80%.
- API drift: all Claude calls in `ai/client.py`, `anthropic` major pinned, structured outputs via `output_config.format`.
- Licensing: JMdict/Warodai attribution, Tatoeba CC BY, KanjiVG CC BY-SA, Kanjium free; attribution page in the Mini App; any subtitle-frequency list needs a license check first.
- Single VPS: nightly `pg_dump` + audio volume backup, Caddy auto-TLS, health checks, `restart: unless-stopped`; the worker is the only component calling paid APIs, so it can be rate-limited independently.
