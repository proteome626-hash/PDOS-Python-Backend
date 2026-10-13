#!/usr/bin/env bash
# Daily backup: PostgreSQL dump + uploads volume, kept for KEEP_DAYS days.
# Usage: sudo bash backup.sh        (installed in cron by install.sh)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

BACKUP_DIR="${BACKUP_DIR:-$PWD/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

DB_FILE="$BACKUP_DIR/db_$STAMP.sql.gz"
UP_FILE="$BACKUP_DIR/uploads_$STAMP.tar.gz"

# Write to .tmp and rename only after success, so a half-written file is never mistaken for a backup.
docker compose exec -T db pg_dump -U pdos -d pdos --clean --if-exists | gzip > "$DB_FILE.tmp"
gzip -t "$DB_FILE.tmp"
mv "$DB_FILE.tmp" "$DB_FILE"

docker compose run --rm --no-deps -T --entrypoint tar backend -czf - -C /app uploads > "$UP_FILE.tmp"
gzip -t "$UP_FILE.tmp"
mv "$UP_FILE.tmp" "$UP_FILE"

find "$BACKUP_DIR" -maxdepth 1 \( -name 'db_*.sql.gz' -o -name 'uploads_*.tar.gz' \) -mtime +"$KEEP_DAYS" -delete
find "$BACKUP_DIR" -maxdepth 1 -name '*.tmp' -mmin +60 -delete

echo "[$(date '+%F %T')] backup OK: $DB_FILE $UP_FILE"
