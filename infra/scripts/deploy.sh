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

# The credential is bind-mounted from the host, so its ownership is host state that no image build
# or deploy can correct. The container runs as an unprivileged user; a key left root-owned mounts
# perfectly and is then unreadable, which surfaces only when a learner taps a syllable and gets
# silence. Checked here, where it is one line of output instead of a support question.
if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-/run/secrets/google-sa.json}" ]]; then
  echo "==> checking the container can read its Google credential"
  if "${COMPOSE[@]}" exec -T api sh -c '[ ! -e "$GOOGLE_APPLICATION_CREDENTIALS" ] || head -c 1 "$GOOGLE_APPLICATION_CREDENTIALS" >/dev/null'; then
    echo "    ok"
  else
    uid="$("${COMPOSE[@]}" exec -T api id -u | tr -d '\r')"
    echo "    UNREADABLE: the api container runs as uid $uid and cannot read the mounted key." >&2
    echo "    Fix on the host:  chown $uid:$uid /etc/nihongo/google-sa.json" >&2
    exit 1
  fi
fi

echo "==> importing the kana seed (upserts on natural keys; safe every deploy)"
"${COMPOSE[@]}" exec -T api nihongo-content import-kana

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
