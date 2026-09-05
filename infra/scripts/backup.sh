#!/usr/bin/env bash
# Nightly backup: pg_dump + audio volume tarball, 14-day retention.
# Cron (as the deploy user):  15 3 * * * /opt/nihongo-tutor/infra/scripts/backup.sh >> /var/log/nihongo-backup.log 2>&1
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
COMPOSE=(docker compose -f "$REPO_ROOT/infra/docker-compose.yml" --env-file "$REPO_ROOT/infra/.env")
STAMP="$(date -u +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"

echo "[$STAMP] pg_dump"
"${COMPOSE[@]}" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' \
  | gzip -9 > "$BACKUP_DIR/db-$STAMP.sql.gz"

echo "[$STAMP] audio volume"
docker run --rm \
  -v nihongo_audio:/data/audio:ro \
  -v "$BACKUP_DIR":/backup \
  alpine:3.20 tar -czf "/backup/audio-$STAMP.tar.gz" -C /data audio

find "$BACKUP_DIR" -type f \( -name 'db-*.sql.gz' -o -name 'audio-*.tar.gz' \) -mtime "+$KEEP_DAYS" -delete
echo "[$STAMP] done: $(du -sh "$BACKUP_DIR" | cut -f1) in $BACKUP_DIR"

# Optional off-box copy (uncomment after `gcloud storage buckets create gs://<bucket>` and granting the VM SA objectAdmin):
# gcloud storage rsync --delete-unmatched-destination-objects "$BACKUP_DIR" "gs://<bucket>/nihongo-backups"
