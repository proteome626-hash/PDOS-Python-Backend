#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────
# PDOS Backend — Ubuntu 22.04+ Deployment Script
# ──────────────────────────────────────────────
# Usage:
#   sudo bash setup.sh                   # interactive
#   sudo bash setup.sh --auto            # non-interactive, prompts for secrets
#   sudo bash setup.sh --seed            # auto + seed demo data
# ──────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${RED}[!]${NC} $1"; }
info() { echo -e "${CYAN}[i]${NC} $1"; }

REPO_URL="${REPO_URL:-https://github.com/your-org/pdos-backend.git}"
BRANCH="${BRANCH:-main}"
APP_DIR="/opt/pdos/backend"
PDOS_USER="pdos"
DB_NAME="pdos"
DB_USER="pdos"

# ── Parse flags ──────────────────────────────
AUTO=false; SEED=false
for arg in "$@"; do
  case "$arg" in --auto) AUTO=true ;; --seed) SEED=true ;; esac
done

# ── Preflight ────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
  warn "This script must be run as root (sudo)."; exit 1
fi

if ! grep -qi "ubuntu" /etc/os-release 2>/dev/null; then
  warn "This script targets Ubuntu. Detected: $(cat /etc/os-release 2>/dev/null | head -1)"
  read -rp "Continue anyway? [y/N] " ans; [[ "$ans" =~ ^[yY] ]] || exit 1
fi

# ── 1. System packages ───────────────────────
info "==> Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
  python3 python3-pip python3-venv python3-dev \
  postgresql postgresql-client redis-server \
  nginx certbot git curl

log "System packages installed"

# ── 2. Create pdos user ──────────────────────
if ! id "$PDOS_USER" &>/dev/null; then
  useradd -m -s /bin/bash "$PDOS_USER"
  log "User '$PDOS_USER' created"
else
  log "User '$PDOS_USER' already exists"
fi

# ── 3. PostgreSQL setup ──────────────────────
info "==> Configuring PostgreSQL..."
pg_isready -q || systemctl start postgresql

if ! su - postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'\" | grep -q 1" 2>/dev/null; then
  DB_PASS="$(openssl rand -base64 24)"
  echo "$DB_PASS" > /root/.pdos_db_pass
  chmod 600 /root/.pdos_db_pass
  su - postgres -c "psql -c \"CREATE USER $DB_USER WITH PASSWORD '${DB_PASS}';\""
  su - postgres -c "psql -c \"CREATE DATABASE $DB_NAME OWNER $DB_USER;\""
  su - postgres -c "psql -c \"GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;\""
  log "PostgreSQL user '$DB_USER' created (password saved to /root/.pdos_db_pass)"
else
  warn "PostgreSQL user '$DB_USER' already exists — skipping DB setup"
  if [ -f /root/.pdos_db_pass ]; then
    DB_PASS="$(cat /root/.pdos_db_pass)"
  else
    warn "No saved password found. Set DATABASE_URL manually in .env"
    DB_PASS=""
  fi
fi

# ── 4. Redis ─────────────────────────────────
info "==> Configuring Redis..."
systemctl enable --now redis-server
log "Redis is running"

# ── 5. Clone / pull repository ───────────────
info "==> Deploying application code..."
if [ -d "$APP_DIR" ]; then
  warn "$APP_DIR already exists — pulling latest"
  cd "$APP_DIR"
  git fetch origin
  git checkout "$BRANCH"
  git pull origin "$BRANCH"
else
  mkdir -p "$(dirname "$APP_DIR")"
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

# ── 6. Python virtual environment ────────────
info "==> Setting up Python virtual environment..."
cd "$APP_DIR"
python3 -m venv venv
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
log "Virtual environment ready"

# ── 7. Environment file ──────────────────────
info "==> Configuring .env..."
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/deploy/.env.production" "$APP_DIR/.env"
  sed -i "s|CHANGE_ME|${DB_PASS}|g" "$APP_DIR/.env"
  if [ ! -f /root/.pdos_secret_key ]; then
    SECRET="$(openssl rand -base64 48)"
    echo "$SECRET" > /root/.pdos_secret_key
  else
    SECRET="$(cat /root/.pdos_secret_key)"
  fi
  sed -i "s|generate-a-random-64-char-secret-key-here|${SECRET}|g" "$APP_DIR/.env"
  log ".env created with generated secrets"
else
  warn "$APP_DIR/.env already exists — skipping"
fi

chown -R "$PDOS_USER":"$PDOS_USER" "$APP_DIR"
chmod 640 "$APP_DIR/.env"

# ── 8. Initialize database schema ────────────
info "==> Initializing database schema..."
su - "$PDOS_USER" -c "cd $APP_DIR && source venv/bin/activate && python scripts/init_prod_db.py"
log "Database schema initialized"

# ── 9. Seed data (optional) ──────────────────
if $SEED; then
  info "==> Seeding demo data..."
  su - "$PDOS_USER" -c "cd $APP_DIR && source venv/bin/activate && python seed_db.py"
  log "Demo data seeded"
fi

# ── 10. systemd service ──────────────────────
info "==> Installing systemd service..."
cp "$APP_DIR/deploy/pdos-backend.service" /etc/systemd/system/pdos-backend.service
systemctl daemon-reload
systemctl enable pdos-backend
systemctl start pdos-backend
log "pdos-backend service started"

# ── 11. Nginx ────────────────────────────────
info "==> Configuring Nginx..."
rm -f /etc/nginx/sites-enabled/default
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/pdos-backend
ln -sf /etc/nginx/sites-available/pdos-backend /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
log "Nginx configured"

# ── 12. Firewall ─────────────────────────────
info "==> Configuring firewall..."
if command -v ufw &>/dev/null; then
  ufw --force reset
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow ssh
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw --force enable
  log "UFW firewall enabled (SSH, HTTP, HTTPS)"
fi

# ── Done ─────────────────────────────────────
echo ""
log "Deployment complete!"
echo ""
echo "  Backend:  http://$(curl -s http://checkip.amazonaws.com 2>/dev/null || hostname -I | awk '{print $1}')"
echo "  Health:   http://YOUR_SERVER/health"
echo "  Logs:     sudo journalctl -u pdos-backend -f"
echo ""
echo "  Next steps:"
echo "    1. Set SSL:  sudo certbot --nginx -d yourdomain.com"
echo "    2. Update CORS_ORIGINS in $APP_DIR/.env"
echo "    3. Restart:  sudo systemctl restart pdos-backend"
echo ""
echo "  Secrets saved to: /root/.pdos_db_pass, /root/.pdos_secret_key"
