# Hand-off: Phase 0 complete, continue with Phase 1

This note lets a fresh Claude Code session (local CLI or desktop app) pick up where the remote
session that built Phase 0 stopped. Read it together with `docs/PLAN.md` and `README.md`.

## State of the repository

- Branch `claude/phase-0-skeleton` holds Phase 0 in five commits; `main` is identical.
- Phase 0 scope from the plan is fully implemented and verified locally: backend (FastAPI + aiogram
  webhook, Alembic baseline, invite flow, admin bootstrap, initData → JWT), Mini App shell, compose
  stack, CI, Makefile, README.
- Verification at hand-off: ruff/black/mypy clean, 43 pytest tests passing against Postgres 16,
  `alembic upgrade → downgrade → upgrade → check` clean, `npm run build` clean, both compose files
  validate. Docker image builds were not exercised (no Docker daemon in the remote sandbox).
- Nothing has been deployed. No bot token, GCP project or domain exist yet.

## Decisions taken during Phase 0 that are not in PLAN.md

- `pyjwt` was added for the short-lived JWT (plan named the JWT but no library).
- `review_logs.state_before` reuses the `card_state` Postgres enum; the migration uses
  `create_type=False` for it. Keep that pattern for shared enums.
- `admin_tg_ids` is parsed from a comma-separated string via `NoDecode` in `app/config.py`.
- Bot handler routers are module-level singletons; tests attach them to one session-scoped
  Dispatcher and swap `dp["sessionmaker"]` per test (see `tests/bot/test_start.py`).
- Integration tests use `TEST_DATABASE_URL` when set, else testcontainers, else skip. CI uses a
  Postgres service container with `TEST_DATABASE_URL`.
- All learner-facing strings live in `app/bot/texts_ru.py`; the Mini App has its own Russian
  strings inline for now (to be centralised when the UI grows).

## Local workflow reminders

- `make db-up` (Docker Desktop must be running), `make migrate`, `make test`, `make dev`.
- Model changes require `make revision m="..."` and a review of the generated file; CI runs
  `alembic check` and fails on drift.
- Push with your own git credentials (`gh auth login` as the repo owner). Do not install the Claude
  GitHub App for this repo; see the README section on deployment for why Caddy needs a domain.

## Phase 1 entry point

Suggested first prompt for the new session:

> Read docs/HANDOFF.md, docs/PLAN.md and README.md. Phase 0 is complete. Create branch
> `claude/phase-1-kana` from `main` and implement Phase 1 (kana bootcamp + SRS) exactly as the
> roadmap describes: kana seed data and audio pipeline stubs, FSRS wrapper in `domain/srs.py`,
> session engine v1 (`session_planner.py`, `interleave.py`), chat steps, Mini App kana grid,
> reminders cron, forgiving streak, `/stats`. Write the Phase 1 tests listed in the plan's
> Verification section. Commit in small steps; do not open a PR unless asked.

Phase 1 needs from the owner before deployment (not before coding): Telegram bot token, GCP
project with billing, a domain pointing at the VM. Anthropic/OpenAI keys arrive with Phase 2/3.
