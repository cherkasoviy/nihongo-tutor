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

# The credential is bind-mounted from the host, so its state is host state that no image build or
# deploy can correct. Two ways it goes wrong, and neither is visible from outside the container:
#
#   * the key is root-owned, so the unprivileged app user cannot read it. The stack comes up
#     healthy, every smoke test passes, and the failure appears only when a learner taps a syllable.
#   * the key was absent when the stack first started, so Docker created an empty *directory* at the
#     host path — a bind mount's source is created if missing, and it is created as a directory.
#     This is the ordinary first-deploy mistake: running deploy.sh before putting the key in place.
#
# They need different remedies, and telling someone to chown a directory fixes nothing.
echo "==> checking the container can read its Google credential"
cred_state="$("${COMPOSE[@]}" exec -T api sh -c '
  f="$GOOGLE_APPLICATION_CREDENTIALS"
  if   [ ! -e "$f" ]; then echo absent
  elif [ -d "$f" ];  then echo directory
  elif head -c 1 "$f" >/dev/null 2>&1; then echo ok
  else echo unreadable
  fi' | tr -d '\r')"

case "$cred_state" in
  ok)      echo "    ok" ;;
  absent)  echo "    none deployed (the AI layer is not in use yet)" ;;
  directory)
    echo "    NOT A FILE: the key was missing when the stack first started, so Docker created an" >&2
    echo "    empty directory at the host path in its place." >&2
    echo "    Fix on the host:  rmdir /etc/nihongo/google-sa.json" >&2
    echo "                      then put the real key there and re-run this script." >&2
    exit 1 ;;
  *)
    uid="$("${COMPOSE[@]}" exec -T api id -u | tr -d '\r')"
    echo "    UNREADABLE: the api container runs as uid $uid and cannot read the mounted key." >&2
    echo "    Fix on the host:  chown $uid:$uid /etc/nihongo/google-sa.json" >&2
    exit 1 ;;
esac

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
