# Production cutover

The one-time sequence that takes this repository from a laptop to a server people actually use.
[`README.md`](../README.md#deploying-to-a-docker-host) covers provisioning a host; everything here is
host-agnostic and works on any box that can run Docker. What it adds beyond the README is the order
of operations, the three steps the README leaves out — the content seed, moving existing study
history, and handing the Telegram webhook over from the laptop — and the failure modes that are
expensive to hit.

## Before you touch the server

| | Why it has to be true first |
|---|---|
| `dig +short <domain>` returns the box's IP | Caddy proves domain control over HTTP before Let's Encrypt issues a certificate. Deploying against DNS that has not propagated fails, and repeated failures hit an ACME rate limit measured in hours — this is the one mistake that costs real time |
| TCP 80, TCP 443 and **UDP 443** are open inbound | 80 for the ACME challenge and the redirect, 443 for TLS, UDP 443 for HTTP/3, which the compose file publishes |
| Docker Engine + the compose plugin are installed | `curl -fsSL https://get.docker.com \| sudo sh`, then `sudo usermod -aG docker "$USER" && newgrp docker` |
| ≥ 2 GB RAM **plus swap** (see the next row), ≥ 20 GB disk | Two different numbers get confused here. *Idle* is ~1 GB — Postgres, Redis, api, worker and Caddy. *Peak* is the image build: `deploy.sh` runs `docker compose build` on the server, and the Mini App stage runs `npm run build`, where Vite/esbuild is the memory spike in this stack. On a 2 GB box with no swap that peak is what gets OOM-killed, not the running stack |
| A 2 GB swapfile exists **before the first deploy** | Hetzner CPX12 (1 vCPU, 2 GB) ships with no swap. Create it first — recovering from an OOM-killed build is slower than preventing one:<br><br>`sudo fallocate -l 2G /swapfile`<br>`sudo chmod 600 /swapfile`<br>`sudo mkswap /swapfile`<br>`sudo swapon /swapfile`<br>`echo '/swapfile none swap sw 0 0' \| sudo tee -a /etc/fstab`<br>`echo 'vm.swappiness=10' \| sudo tee /etc/sysctl.d/99-swappiness.conf`<br>`sudo sysctl -w vm.swappiness=10`<br><br>The `/etc/fstab` line makes it survive a reboot; `vm.swappiness=10` keeps the kernel using RAM in preference to swap, so swap is there for the build peak rather than slowing the steady state. Verify with `free -h` |

## 1. Clone and configure

```bash
sudo mkdir -p /opt/nihongo-tutor && sudo chown "$USER" /opt/nihongo-tutor
git clone https://github.com/cherkasoviy/nihongo-tutor.git /opt/nihongo-tutor
cd /opt/nihongo-tutor
cp infra/.env.example infra/.env
```

Generate the secrets **on the server** so they never exist on a laptop or in a shell history you sync:

```bash
openssl rand -hex 32   # WEBHOOK_SECRET
openssl rand -hex 32   # JWT_SECRET
openssl rand -hex 24   # POSTGRES_PASSWORD
```

| Variable | Value |
|---|---|
| `DOMAIN` | `tutor.example.com` — bare host, no scheme |
| `PUBLIC_URL`, `MINIAPP_URL` | `https://tutor.example.com` |
| `POSTGRES_PASSWORD` | the generated value — **and paste the same value into `DB_URL`**, which embeds it |
| `BOT_TOKEN` | from @BotFather |
| `WEBHOOK_SECRET`, `JWT_SECRET` | the generated values |
| `ADMIN_TG_IDS` | your Telegram user id; whoever is listed becomes admin on first `/start` |
| everything under *AI providers* | leave empty — Phase 2 |

`infra/.env` is git-ignored and must stay that way. `PUBLIC_URL` must be `https://`: the API refuses
to start in prod otherwise, because Telegram will not deliver webhooks over plaintext.

## 2. Deploy

```bash
infra/scripts/deploy.sh --no-pull
```

The script builds both images, publishes the Mini App bundle into the volume Caddy serves, starts
the stack — the `api` container runs `alembic upgrade head` before uvicorn — waits for health,
imports the kana seed, then smoke-tests `/healthz` and `getWebhookInfo`. Later deploys drop
`--no-pull` so it fetches the branch first.

The seed import is part of the script and upserts on natural keys, so it is safe on every deploy. It
has to be there: migrations create tables, not content, and an empty `kana` table means a bot that
starts a lesson and has nothing to put in it.

If the build still runs out of memory even with swap, build the Mini App image on your laptop
(`docker build -f infra/miniapp.Dockerfile -t nihongo-miniapp:local .`), push or `docker save | ssh …
docker load` it onto the box, and deploy with `--no-pull` so the server reuses the image instead of
rebuilding it.

Then in @BotFather: `/newapp` → the bot → title, description, a 640×360 image → Web App URL
`https://<domain>`. Without this the Mini App button has nowhere to open.

## 3. Hand the webhook over from the laptop

**A bot token has exactly one webhook URL.** Development pointed it at an ephemeral cloudflared
tunnel; the moment production boots it calls `setWebhook` with the new URL and Telegram forgets the
tunnel. Two consequences:

- **The local bot goes silent after the cutover, and stays silent.** That is the intent, but shut
  the local stack down rather than leaving it running and confusing — and know that restarting it
  later steals the webhook *back* from production, silently. If you want to keep developing against
  a tunnel afterwards, use a second bot token from @BotFather.
- **Queued updates are delivered, not dropped** (`drop_pending_updates=False`). Anything that piled
  up while the tunnel was down arrives at production on registration.

Old messages in your chat history keep their inline keyboards — Telegram never expires them — and
their buttons carry step ids from the *dev* database. Pressing one after the cutover answers
«Этот шаг уже неактуален» rather than failing, so this is a cosmetic annoyance, not a bug. Start a
fresh `/start` and work from the new messages.

Verify what Telegram actually thinks:

```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env exec -T api \
  python -c "
import asyncio, os
from aiogram import Bot
async def main():
    bot = Bot(os.environ['BOT_TOKEN'])
    print(await bot.get_webhook_info())
    await bot.session.close()
asyncio.run(main())"
```

`url` must be `https://<domain>/tg/webhook`, `pending_update_count` should settle at 0, and
`last_error_message` should be `None`. A non-empty error here is almost always TLS: check
`docker compose … logs caddy`.

## 4. Move existing study history

Card rows cannot be copied between databases. `cards.item_id` points at `items.id`, a UUID minted
when `import-kana` runs, so あ has a different id in every database that ever imported the seed — a
`pg_dump` of the learner tables would arrive pointing at rows that do not exist. `export-progress`
travels by `(script, char, direction)` instead and the importer resolves against whatever ids the
destination minted.

**Fix the timezone before exporting.** It rides along in the payload, and the reminder job fires on
it. Check it in the Mini App's Настройки tab — the Mini App adopts the browser's zone on login while
the learner is still on the default, so a laptop reporting the wrong zone silently moves the
reminder.

On the laptop, with the dev database running:

```bash
cd backend && uv run nihongo-content export-progress --tg-id <your-tg-id> -o progress.json
scp progress.json <user>@<host>:/opt/nihongo-tutor/
```

On the server:

```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env cp \
  progress.json api:/tmp/progress.json
docker compose -f infra/docker-compose.yml --env-file infra/.env exec -T api \
  nihongo-content import-progress -i /tmp/progress.json
```

It is idempotent — cards dedupe on `(learner, item, direction)`, reviews on
`(card, review_at, rating)`, sessions on `(learner, started_at, kind)` — so run it again after any
further local study without producing duplicates. It exits non-zero rather than silently dropping a
card if the destination does not know a syllable, which means the seed import in step 2 succeeded.
`progress.json` contains real learner history: delete both copies afterwards.

## 5. Check it end to end

1. `curl -fsS https://<domain>/healthz`
2. `/start` in Telegram → the greeting, and the Mini App button opens
3. `/admin invite` → a link; that link is how the second learner joins
4. Start a lesson, answer a step, and confirm the Mini App's progress screen agrees with the bot
5. `docker compose … exec api alembic current` matches `head`

## 6. Before you walk away

```bash
crontab -e
# 15 3 * * * /opt/nihongo-tutor/infra/scripts/backup.sh >> /var/log/nihongo-backup.log 2>&1
```

`backup.sh` writes a `pg_dump` and an audio tarball into `./backups` with 14-day retention, and
`restore.sh` reads them back. Content is reproducible from the seed files in git; **learner history
is the only thing a backup protects**, and after step 4 the server holds the only copy that matters.
Copy the backups off the box — the commented `gcloud storage rsync` line in `backup.sh` is one way,
any object store works.

## When something is wrong

| Symptom | Look at |
|---|---|
| No certificate, connection refused on 443 | `docker compose … logs caddy` — usually DNS not resolving yet, or 80/443 blocked upstream |
| Bot silent, `/healthz` fine | `getWebhookInfo` (step 3). A laptop instance that came back up is the usual thief |
| `502` from `/api/*` | api container unhealthy: `docker compose … logs api`, then `alembic current` |
| Lesson starts but offers nothing | the seed did not import: re-run `deploy.sh`, or `exec api nihongo-content import-kana` |
| Roll back | `git checkout <previous sha> && infra/scripts/deploy.sh --no-pull`. Migrations are forward-only in practice — check `alembic downgrade` is safe before relying on it |
