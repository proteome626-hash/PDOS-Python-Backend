#!/usr/bin/env bash
# ==============================================================================
# PDOS Backend - One-Command Server Runner
# GitHub: https://github.com/proteome626-hash/PDOS-Python-Backend
#
# Usage:
#   sudo bash run.sh [options]
#
# Options:
#   --sqlite <file>          Migrate data from SQLite database to PostgreSQL
#   --restore-db <file>      Restore PostgreSQL dump (.sql.gz)
#   --restore-uploads <file> Restore uploads archive (.tar.gz)
# ==============================================================================
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/proteome626-hash/PDOS-Python-Backend.git}"
BRANCH="${BRANCH:-main}"

# Colors for friendly output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
info() { echo -e "${BLUE}[i]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
die()  { echo -e "${RED}[✗]${NC} $1" >&2; exit 1; }

# Auto-elevate to root if not already root
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        info "Elevating privileges to root..."
        exec sudo bash "$0" "$@"
    else
        die "Please run this script as root or with sudo."
    fi
fi

# ── 1. Check & Install System Dependencies (Docker, Compose, Git, Cron) ───────
info "Step 1/6: Checking system dependencies..."

if ! command -v curl >/dev/null 2>&1 || ! command -v openssl >/dev/null 2>&1 || ! command -v cron >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then
    info "Installing basic packages (curl, openssl, cron, git)..."
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl git openssl cron >/dev/null
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    warn "Docker / Docker Compose not found. Installing official Docker..."
    curl -fsSL https://get.docker.com | sh
fi

systemctl enable --now docker >/dev/null 2>&1 || service docker start >/dev/null 2>&1 || true
systemctl enable --now cron >/dev/null 2>&1 || service cron start >/dev/null 2>&1 || true
log "Docker ($(docker --version | awk '{print $3}' | tr -d ,)) and Git are ready"

# ── 2. Locate / Clone Codebase & Link to GitHub ───────────────────────────────
info "Step 2/6: Linking repository to GitHub ($REPO_URL)..."
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

if [ -f "$SCRIPT_DIR/docker-compose.yml" ]; then
    APP_DIR="$SCRIPT_DIR"
else
    APP_DIR="${APP_DIR:-/opt/pdos}"
    if [ ! -d "$APP_DIR/.git" ]; then
        info "Cloning project from GitHub into $APP_DIR..."
        mkdir -p "$APP_DIR"
        git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    fi
fi

cd "$APP_DIR"

# Ensure Git remote origin is configured for updates
if [ -d "$APP_DIR/.git" ]; then
    git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
    git config core.fileMode false 2>/dev/null || true
    if git remote get-url origin >/dev/null 2>&1; then
        CURRENT_ORIGIN="$(git remote get-url origin)"
        if [ "$CURRENT_ORIGIN" != "$REPO_URL" ]; then
            git remote set-url origin "$REPO_URL"
        fi
    else
        git remote add origin "$REPO_URL"
    fi
fi

# Ensure executable permissions for all shell scripts
chmod +x install.sh update.sh backup.sh run.sh deploy/*.sh 2>/dev/null || true
log "Project directory is ready at $APP_DIR"

# Parse CLI options
SQLITE=""; RESTORE_DB=""; RESTORE_UPLOADS=""; AUTO_UPDATE=false
while [ $# -gt 0 ]; do
  case "$1" in
    --sqlite) SQLITE="$(readlink -f "${2:?--sqlite requires a file path}")"; shift 2 ;;
    --restore-db) RESTORE_DB="$(readlink -f "${2:?--restore-db requires a file path}")"; shift 2 ;;
    --restore-uploads) RESTORE_UPLOADS="$(readlink -f "${2:?--restore-uploads requires a file path}")"; shift 2 ;;
    --auto-update) AUTO_UPDATE=true; shift ;;
    *) die "Unknown option: $1" ;;
  esac
done

# If no --sqlite passed but pdos.db exists in current directory or /tmp/pdos.db, auto-detect it
if [ -z "$SQLITE" ] && [ -z "$RESTORE_DB" ]; then
    if [ -f "$APP_DIR/pdos.db" ]; then
        SQLITE="$APP_DIR/pdos.db"
        info "Detected local pdos.db — will migrate to PostgreSQL"
    elif [ -f "/tmp/pdos.db" ]; then
        SQLITE="/tmp/pdos.db"
        info "Detected /tmp/pdos.db — will migrate to PostgreSQL"
    fi
fi

# ── 3. Environment Configuration (.env) ───────────────────────────────────────
info "Step 3/6: Checking environment secrets (.env)..."
if [ ! -f .env ]; then
    info "Generating production .env with secure random keys..."
    cat > .env <<EOF
DB_PASSWORD=$(openssl rand -hex 24)
SECRET_KEY=$(openssl rand -hex 32)
CORS_ORIGINS=*
UVICORN_WORKERS=4
LOG_LEVEL=info
EOF
    chmod 600 .env
    log "Created .env with secure random credentials"
else
    log "Found existing .env file"
fi

# ── 4. Start Database & Optional Migration ─────────────────────────────────────
info "Step 4/6: Building and starting database services..."
docker compose build --quiet
docker compose up -d --wait db redis

if [ -n "$SQLITE" ] && [ -f "$SQLITE" ]; then
    info "Migrating SQLite data from $SQLITE to PostgreSQL..."
    docker compose run --rm --no-deps -T -v "$SQLITE:/import/pdos.db:ro" \
        --entrypoint python backend scripts/sqlite_to_postgres.py /import/pdos.db
    log "SQLite data successfully migrated to PostgreSQL"
fi

if [ -n "$RESTORE_DB" ] && [ -f "$RESTORE_DB" ]; then
    info "Restoring PostgreSQL dump from $RESTORE_DB..."
    gunzip -c "$RESTORE_DB" | docker compose exec -T db psql -q -U pdos -d pdos -v ON_ERROR_STOP=1 >/dev/null
    log "Database successfully restored from $RESTORE_DB"
fi

if [ -n "$RESTORE_UPLOADS" ] && [ -f "$RESTORE_UPLOADS" ]; then
    info "Restoring uploads archive from $RESTORE_UPLOADS..."
    docker compose run --rm --no-deps -T --entrypoint tar backend -xzf - -C /app < "$RESTORE_UPLOADS"
    log "Uploads restored from $RESTORE_UPLOADS"
fi

# ── 5. Start Application & Reverse Proxy ──────────────────────────────────────
info "Step 5/6: Starting FastAPI Application and Nginx..."
docker compose up -d

info "Waiting for service to become healthy..."
SERVER_OK=false
for i in $(seq 1 45); do
    if curl -fsS -o /dev/null http://127.0.0.1/ >/dev/null 2>&1; then
        SERVER_OK=true
        break
    fi
    sleep 2
done

if [ "$SERVER_OK" != "true" ]; then
    warn "Application is starting up or returned non-200. Container logs:"
    docker compose logs --tail=30 backend
    die "App failed to respond on http://127.0.0.1/. Check logs above."
fi
log "Application is fully responsive!"

# ── 6. Schedule Automated Daily Backup & Auto-Update ───────────────────────────
info "Step 6/6: Configuring scheduled background tasks..."
if [ -f "$APP_DIR/backup.sh" ]; then
    echo "0 2 * * * root bash $APP_DIR/backup.sh >> /var/log/pdos-backup.log 2>&1" > /etc/cron.d/pdos-backup
    chmod 644 /etc/cron.d/pdos-backup
    log "Daily backup scheduled at 02:00 AM (kept 14 days in $APP_DIR/backups)"
fi

if [ "$AUTO_UPDATE" = "true" ] && [ -f "$APP_DIR/auto-update.sh" ]; then
    bash "$APP_DIR/auto-update.sh" --install
fi

# ── Summary & Dashboard ───────────────────────────────────────────────────────
HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "YOUR_SERVER_IP")

echo
echo -e "${GREEN}================================================================${NC}"
echo -e "${GREEN}            PDOS Backend is running successfully!               ${NC}"
echo -e "${GREEN}================================================================${NC}"
echo -e "  🌐 App URL:       ${BLUE}http://${HOST_IP}/${NC}"
echo -e "  📖 Swagger Docs:  ${BLUE}http://${HOST_IP}/docs${NC}"
echo -e "  🐙 GitHub Repo:   ${BLUE}$REPO_URL${NC}"
echo -e "  🔒 Database:      PostgreSQL (Container: pdos-db)"
echo -e "  ⚡ Cache/PubSub:  Redis (Container: pdos-redis)"
echo -e "  ⚙️ Reverse Proxy:  Nginx (Port 80)"
echo
echo -e "Useful Commands:"
echo -e "  • View live logs:      ${YELLOW}docker compose logs -f backend${NC}"
echo -e "  • Restart app:         ${YELLOW}docker compose restart${NC}"
echo -e "  • Stop app:            ${YELLOW}docker compose down${NC}"
echo -e "  • Pull updates now:    ${YELLOW}sudo ./update.sh${NC}"
echo -e "  • Enable auto-pull:    ${YELLOW}sudo ./auto-update.sh --install${NC}"
echo -e "  • Manual backup now:   ${YELLOW}sudo ./backup.sh${NC}"
echo -e "${GREEN}================================================================${NC}"
