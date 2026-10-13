#!/usr/bin/env bash
# One-time install on a clean Ubuntu server (no Python/Postgres needed — everything runs in Docker).
#
# Usage (as root):
#   REPO_URL=https://github.com/USER/REPO.git bash install.sh [options]
#   # or, from inside an existing clone:  sudo bash install.sh [options]
#
# Options (first install only, pick at most one DB source):
#   --sqlite FILE            migrate data from the old SQLite pdos.db (row counts verified)
#   --restore-db FILE        restore a db_*.sql.gz produced by backup.sh
#   --restore-uploads FILE   restore an uploads tar.gz (backup.sh output, or `tar -czf uploads.tar.gz uploads`)
#
# Env: REPO_URL, BRANCH (default main), APP_DIR (default /opt/pdos)
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/proteome626-hash/PDOS-Python-Backend.git}"
BRANCH="${BRANCH:-main}"

log() { echo -e "\033[0;32m[✓]\033[0m $1"; }
die() { echo -e "\033[0;31m[!]\033[0m $1" >&2; exit 1; }

SQLITE=""; RESTORE_DB=""; RESTORE_UPLOADS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --sqlite) SQLITE="$(readlink -f "${2:?--sqlite needs a file}")"; shift 2 ;;
    --restore-db) RESTORE_DB="$(readlink -f "${2:?--restore-db needs a file}")"; shift 2 ;;
    --restore-uploads) RESTORE_UPLOADS="$(readlink -f "${2:?--restore-uploads needs a file}")"; shift 2 ;;
    *) die "Unknown option: $1" ;;
  esac
done
[ "$(id -u)" -eq 0 ] || die "Run as root (sudo)."
[ -n "$SQLITE" ] && [ -n "$RESTORE_DB" ] && die "Use either --sqlite or --restore-db, not both."
for f in "$SQLITE" "$RESTORE_DB" "$RESTORE_UPLOADS"; do
  [ -z "$f" ] || [ -f "$f" ] || die "File not found: $f"
done

# ── 1. Docker + Git ──────────────────────────
apt-get update -qq
apt-get install -y -qq ca-certificates curl git openssl cron >/dev/null
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker cron >/dev/null
log "Docker $(docker --version | awk '{print $3}' | tr -d ,) + Git installed"

# ── 2. Code ──────────────────────────────────
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
if [ -f "$SCRIPT_DIR/docker-compose.yml" ] && [ -d "$SCRIPT_DIR/.git" ]; then
  APP_DIR="$SCRIPT_DIR"
else
  APP_DIR="${APP_DIR:-/opt/pdos}"
  if [ ! -d "$APP_DIR/.git" ]; then
    [ -n "${REPO_URL:-}" ] || die "Set REPO_URL (private repo: use a deploy key or https://TOKEN@github.com/...)."
    git clone --branch "${BRANCH:-main}" "$REPO_URL" "$APP_DIR"
  fi
fi
cd "$APP_DIR"
git config --global --add safe.directory "$APP_DIR"
git config core.fileMode false
chmod +x install.sh update.sh backup.sh
log "Code at $APP_DIR"

# ── 3. Secrets (.env, never committed) ───────
if [ ! -f .env ]; then
  # hex only: the password is embedded in DATABASE_URL, so no URL-special characters.
  cat > .env <<EOF
DB_PASSWORD=$(openssl rand -hex 24)
SECRET_KEY=$(openssl rand -hex 32)
CORS_ORIGINS=*
UVICORN_WORKERS=4
LOG_LEVEL=info
EOF
  log ".env generated with random secrets"
else
  log ".env exists — kept"
fi
chmod 600 .env

# ── 4. Database + data import ────────────────
docker compose build
docker compose up -d --wait db redis

if [ -n "$SQLITE" ]; then
  docker compose run --rm --no-deps -T -v "$SQLITE:/import/pdos.db:ro" \
    --entrypoint python backend scripts/sqlite_to_postgres.py /import/pdos.db
  log "SQLite data migrated"
fi
if [ -n "$RESTORE_DB" ]; then
  gunzip -c "$RESTORE_DB" | docker compose exec -T db psql -q -U pdos -d pdos -v ON_ERROR_STOP=1 >/dev/null
  log "Database restored from $RESTORE_DB"
fi
if [ -n "$RESTORE_UPLOADS" ]; then
  docker compose run --rm --no-deps -T --entrypoint tar backend -xzf - -C /app < "$RESTORE_UPLOADS"
  log "Uploads restored from $RESTORE_UPLOADS"
fi

# ── 5. Start everything ──────────────────────
docker compose up -d
for _ in $(seq 1 60); do
  curl -fsS -o /dev/null http://127.0.0.1/ && break
  sleep 2
done
curl -fsS -o /dev/null http://127.0.0.1/ || die "App not responding. Check: docker compose logs backend"
log "App running on http://$(hostname -I | awk '{print $1}')/"

# ── 6. Daily backup at 02:00 ─────────────────
echo "0 2 * * * root bash $APP_DIR/backup.sh >> /var/log/pdos-backup.log 2>&1" > /etc/cron.d/pdos-backup
chmod 644 /etc/cron.d/pdos-backup
log "Daily backup scheduled (02:00, kept 14 days in $APP_DIR/backups)"

echo
echo "Update later with:  sudo bash $APP_DIR/update.sh"
