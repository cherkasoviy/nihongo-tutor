#!/usr/bin/env bash
# Restore a pg_dump made by backup.sh into the running postgres container.
#   infra/scripts/restore.sh backups/db-20260905-031500.sql.gz
set -euo pipefail

[[ $# -eq 1 ]] || { echo "usage: $0 <db-*.sql.gz>" >&2; exit 1; }
DUMP="$1"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose -f "$REPO_ROOT/infra/docker-compose.yml" --env-file "$REPO_ROOT/infra/.env")

read -r -p "This DROPS and recreates the database from $DUMP. Type 'restore' to continue: " answer
[[ "$answer" == "restore" ]] || exit 1

"${COMPOSE[@]}" stop api worker
"${COMPOSE[@]}" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\"" -c "CREATE DATABASE \"$POSTGRES_DB\""'
gunzip -c "$DUMP" | "${COMPOSE[@]}" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q'
"${COMPOSE[@]}" start api worker
echo "restored"
