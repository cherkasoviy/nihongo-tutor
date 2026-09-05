# Nihongo Tutor

A Japanese-learning platform for one learner (and a few invited friends): a Telegram bot for the daily
habit loop plus a Telegram Mini App for the rich UI. Built on second-language-acquisition research
(FSRS spaced repetition, retrieval practice, i+1 input, recasts), Russian as the interface language,
no romaji anywhere. The full design is in [`docs/PLAN.md`](docs/PLAN.md).

**Status: Phase 0 (skeleton).** Invite-only onboarding, admin bootstrap, Mini App auth and the
deployable stack exist; the learning content and session engine arrive in Phase 1+.

## Layout

```
backend/    FastAPI + aiogram 3 (webhook) + SQLAlchemy 2 async + Alembic + arq  (Python 3.12, uv)
miniapp/    React 18 + TypeScript + Vite + @telegram-apps/sdk-react
infra/      docker-compose (caddy, api, worker, postgres, redis), Caddyfile, Dockerfiles, scripts
docs/       PLAN.md (approved design and roadmap)
Makefile    dev / test / migrate / deploy entry points (`make help`)
```

## Local development

Prerequisites: [uv](https://docs.astral.sh/uv/), Node 22, Docker (for Postgres/Redis and the
testcontainers-based tests). Python 3.12 is installed by uv automatically.

```bash
make setup          # uv sync + npm install
make db-up          # Postgres 16 + Redis 7 in Docker (ports 5432 / 6379)
cp infra/.env.example backend/.env
```

Edit `backend/.env` for local use:

```dotenv
ENV=dev
DB_URL=postgresql+asyncpg://nihongo:nihongo@localhost:5432/nihongo
REDIS_URL=redis://localhost:6379/0
PUBLIC_URL=http://localhost:8000        # not https -> webhook is not registered, API still runs
MINIAPP_URL=http://localhost:5173
BOT_TOKEN=<token from @BotFather>       # leave empty to run the API without Telegram
WEBHOOK_SECRET=$(openssl rand -hex 32)
JWT_SECRET=$(openssl rand -hex 32)
ADMIN_TG_IDS=<your Telegram user id>
```

Then:

```bash
make migrate        # alembic upgrade head
make dev            # API on :8000 (autoreload) + Vite on :5173 (proxies /api to :8000)
make worker         # in another terminal: arq worker (reminder cron is a no-op until Phase 1)
```

Useful URLs: `http://localhost:8000/healthz`, `http://localhost:8000/api/docs`.

To exercise the bot locally you need a public https URL for the webhook (Telegram will not call
`http://localhost`). Use a tunnel (`cloudflared tunnel --url http://localhost:8000` or ngrok), set
`PUBLIC_URL` to the tunnel URL and `MINIAPP_URL` to the tunnel URL of the Vite server, restart the
API, and the lifespan registers the webhook. In @BotFather set the Mini App URL to the same
`MINIAPP_URL` (`/newapp` or Bot Settings -> Menu Button).

### Tests and checks

```bash
make test           # pytest: unit + integration (Postgres via TEST_DATABASE_URL or testcontainers)
make test-unit      # no database needed
make lint           # ruff, black --check, tsc
make typecheck      # mypy --strict on backend/app
make check          # everything CI runs
```

Integration tests use `TEST_DATABASE_URL` when set (any reachable Postgres), otherwise start a
`postgres:16-alpine` testcontainer, otherwise skip. `make db-up` and
`TEST_DATABASE_URL=postgresql+asyncpg://nihongo:nihongo@localhost:5432/nihongo make test` is the
fastest loop.

Phase 0 test coverage: initData HMAC (valid / expired / tampered / wrong token), JWT issue and
verify, webhook secret-token check, concurrent redemption of a `max_uses=1` invite
(`SELECT ... FOR UPDATE`), API auth exchange and admin guard, `/start` and `/admin` handlers through
the real Dispatcher with a network-less bot.

### Database migrations

```bash
make revision m="add streaks"   # autogenerate from models (review the file!)
make migrate                    # apply
make downgrade                  # one step back
```

Models live in `backend/app/db/models/`; every module must be imported in
`backend/app/db/models/__init__.py` so autogenerate sees it. CI runs `alembic upgrade head` and
`alembic check` on an empty Postgres, so a model change without a migration fails the build.

## How onboarding works (Phase 0)

1. Users listed in `ADMIN_TG_IDS` become admins on their first `/start` (existing rows are promoted
   at API start).
2. An admin creates an invite: in chat `/admin invite [max_uses] [days]` or in the Mini App admin
   tab. The bot replies with `https://t.me/<bot>?start=<CODE>`.
3. A learner opens the link or sends `/start <CODE>`. Redemption locks the invite row, so a
   single-use code admits exactly one person even under concurrent taps.
4. The Mini App calls `POST /api/auth/telegram` with Telegram `initData`; the backend validates the
   HMAC with the bot token and returns a one-hour JWT for `/api/*`. Unregistered users get 403 and
   are told to use the bot's invite flow.

All learner-facing text lives in `backend/app/bot/texts_ru.py`.

## Deploying to GCP (e2-medium)

One VM runs everything through docker-compose; Caddy obtains TLS certificates automatically.
Everything below is standard Docker, so moving to another provider later is `pg_dump` + copying the
audio volume.

### 1. Prerequisites (your side)

- Telegram bot token from @BotFather. After deploy: `/newapp` (or Bot Settings -> Menu Button) with
  the Mini App URL `https://<domain>`.
- A domain (or subdomain) you can point at the VM.
- GCP project with billing enabled and `gcloud` installed locally.
- Later phases: Anthropic API key, OpenAI API key, Text-to-Speech API enabled in the project.

### 2. Create the VM

```bash
export PROJECT=<gcp-project-id> ZONE=europe-west3-c REGION=europe-west3 NAME=nihongo
gcloud config set project "$PROJECT"

# Service account the VM runs as; TTS role is enough for the app, no JSON key ever leaves GCP.
gcloud iam service-accounts create nihongo-vm --display-name "Nihongo Tutor VM"
gcloud services enable texttospeech.googleapis.com compute.googleapis.com
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:nihongo-vm@$PROJECT.iam.gserviceaccount.com" \
  --role roles/serviceusage.serviceUsageConsumer

gcloud compute addresses create "$NAME-ip" --region "$REGION"
gcloud compute instances create "$NAME" \
  --zone "$ZONE" --machine-type e2-medium \
  --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
  --boot-disk-size 30GB --boot-disk-type pd-balanced \
  --address "$NAME-ip" --tags http-server,https-server \
  --service-account "nihongo-vm@$PROJECT.iam.gserviceaccount.com" \
  --scopes cloud-platform

gcloud compute firewall-rules create allow-http-https \
  --allow tcp:80,tcp:443,udp:443 --target-tags http-server,https-server 2>/dev/null || true
gcloud compute addresses describe "$NAME-ip" --region "$REGION" --format 'value(address)'
```

Create an `A` record for your domain pointing at the printed address. Wait until `dig +short
<domain>` returns it (Caddy needs this to issue the certificate).

### 3. Prepare the VM

```bash
gcloud compute ssh "$NAME" --zone "$ZONE"
# on the VM:
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER" && newgrp docker
sudo mkdir -p /opt/nihongo-tutor && sudo chown "$USER" /opt/nihongo-tutor
git clone https://github.com/cherkasoviy/nihongo-tutor.git /opt/nihongo-tutor
cd /opt/nihongo-tutor
cp infra/.env.example infra/.env
```

Fill `infra/.env`:

| Variable | Value |
|---|---|
| `DOMAIN`, `PUBLIC_URL`, `MINIAPP_URL` | your domain / `https://<domain>` / `https://<domain>` |
| `POSTGRES_PASSWORD` and the same password inside `DB_URL` | `openssl rand -hex 24` |
| `BOT_TOKEN` | from @BotFather |
| `WEBHOOK_SECRET`, `JWT_SECRET` | `openssl rand -hex 32` each |
| `ADMIN_TG_IDS` | your Telegram user id (ask @userinfobot) |
| `GOOGLE_APPLICATION_CREDENTIALS` | leave empty on GCP: the attached service account is used |

Secrets stay in that file only; it is git-ignored.

### 4. Deploy

```bash
infra/scripts/deploy.sh --no-pull
```

The script builds the images, publishes the Mini App bundle into Caddy's volume, starts the stack
(the `api` container runs `alembic upgrade head` on boot, then registers the Telegram webhook), waits
for health, and smoke-tests `https://<domain>/healthz` and `getWebhookInfo`. Subsequent deploys are
`infra/scripts/deploy.sh` (pulls the branch first) or `make deploy`.

Then in @BotFather: `/newapp` -> pick the bot -> title, description, a 640x360 image -> Web App URL
`https://<domain>`. Send `/start` to the bot: as the id in `ADMIN_TG_IDS` you are the admin, and
`/admin invite` produces the first invite link.

### 5. Operations

```bash
make compose-logs                                 # follow logs
docker compose -f infra/docker-compose.yml --env-file infra/.env ps
docker compose -f infra/docker-compose.yml --env-file infra/.env exec api alembic current
infra/scripts/backup.sh                           # pg_dump + audio tarball -> ./backups (14-day retention)
infra/scripts/restore.sh backups/db-<stamp>.sql.gz
```

Nightly backup cron (as the deploy user): `15 3 * * * /opt/nihongo-tutor/infra/scripts/backup.sh >> /var/log/nihongo-backup.log 2>&1`.
Uncomment the `gcloud storage rsync` line in `backup.sh` to copy backups off the VM.

Memory budget on 4 GB: Postgres 256 MB shared buffers, Redis capped at 192 MB, api + worker
~300-500 MB each; the rest is headroom for Sudachi dictionaries in Phase 2.

## Roadmap

See the phased roadmap in [`docs/PLAN.md`](docs/PLAN.md#phased-roadmap). Phase 1 (kana bootcamp +
FSRS + session engine v1 + reminders + streaks) is next.

## Licenses of bundled data (from Phase 1)

JMdict/JMnedict (EDRDG, CC BY-SA 4.0), Warodai (CC BY-SA), Tatoeba (CC BY 2.0 FR), KanjiVG
(CC BY-SA 3.0), Kanjium (free). An attribution page ships in the Mini App with the first import.
