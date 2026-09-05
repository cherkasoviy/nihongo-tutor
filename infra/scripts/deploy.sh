#!/usr/bin/env bash
# Deploy or update the stack on the VM. Idempotent; safe to re-run.
#   infra/scripts/deploy.sh            # pull latest from the current branch, rebuild, restart
#   infra/scripts/deploy.sh --no-pull  # rebuild what is checked out
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose -f "$REPO_ROOT/infra/docker-compose.yml" --env-file "$REPO_ROOT/infra/.env")
cd "$REPO_ROOT"

if [[ ! -f infra/.env ]]; then
  echo "infra/.env is missing: cp infra/.env.example infra/.env and fill it in" >&2
  exit 1
fi

if [[ "${1:-}" != "--no-pull" ]]; then
  git pull --ff-only
fi

echo "==> building images"
"${COMPOSE[@]}" build --pull

echo "==> publishing Mini App bundle"
"${COMPOSE[@]}" up --no-deps miniapp

echo "==> starting services (api runs alembic upgrade head on boot)"
"${COMPOSE[@]}" up -d --remove-orphans postgres redis api worker caddy

echo "==> waiting for api health"
for _ in $(seq 1 30); do
  status="$("${COMPOSE[@]}" ps --format '{{.Health}}' api 2>/dev/null || true)"
  [[ "$status" == "healthy" ]] && break
  sleep 2
done
"${COMPOSE[@]}" ps

DOMAIN="$(grep -E '^DOMAIN=' infra/.env | cut -d= -f2-)"
if [[ -n "$DOMAIN" ]]; then
  echo "==> smoke: https://$DOMAIN/healthz"
  curl -fsS "https://$DOMAIN/healthz" && echo
  echo "==> smoke: getWebhookInfo"
  "${COMPOSE[@]}" exec -T api python - <<'PY'
import asyncio, os
from aiogram import Bot
async def main():
    bot = Bot(os.environ["BOT_TOKEN"])
    info = await bot.get_webhook_info()
    print(f"url={info.url} pending={info.pending_update_count} last_error={info.last_error_message!r}")
    await bot.session.close()
asyncio.run(main())
PY
fi

echo "==> pruning dangling images"
docker image prune -f >/dev/null
echo "done"
