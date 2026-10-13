#!/usr/bin/env bash
# ==============================================================================
# PDOS Backend - Automated Background Pull & Update
#
# Perfect for servers behind NAT/router:
# Periodically checks GitHub (git fetch). If a new commit is detected on 'main',
# it automatically triggers update.sh (backup -> pull -> rebuild -> migrate -> test).
#
# Usage:
#   sudo bash auto-update.sh            (Run check once)
#   sudo bash auto-update.sh --install  (Install automatic cron check every minute)
#   sudo bash auto-update.sh --remove   (Remove the cron job)
# ==============================================================================
set -euo pipefail

APP_DIR="$(dirname "$(readlink -f "$0")")"
LOCK_FILE="/tmp/pdos-autoupdate.lock"
CRON_FILE="/etc/cron.d/pdos-autoupdate"
LOG_FILE="/var/log/pdos-autoupdate.log"
BRANCH="main"

cd "$APP_DIR"

# ── Manage Cron Installation ──────────────────────────────────────────────────
if [ "${1:-}" = "--install" ]; then
    [ "$(id -u)" -eq 0 ] || { echo "Please run as root (sudo bash auto-update.sh --install)"; exit 1; }
    echo "* * * * * root bash $APP_DIR/auto-update.sh >> $LOG_FILE 2>&1" > "$CRON_FILE"
    chmod 644 "$CRON_FILE"
    echo "[✓] Automated update installed! Server checks GitHub every 60 seconds."
    echo "[✓] Logs will be written to: $LOG_FILE"
    exit 0
fi

if [ "${1:-}" = "--remove" ]; then
    [ "$(id -u)" -eq 0 ] || { echo "Please run as root (sudo bash auto-update.sh --remove)"; exit 1; }
    rm -f "$CRON_FILE"
    echo "[✓] Automated update cron job removed."
    exit 0
fi

# ── Prevent Concurrent Runs ───────────────────────────────────────────────────
exec 200>"$LOCK_FILE"
if command -v flock >/dev/null 2>&1; then
    flock -n 200 || exit 0
fi

# ── Check for Updates ─────────────────────────────────────────────────────────
if [ ! -d ".git" ]; then
    echo "[!] Not a git repository: $APP_DIR"
    exit 1
fi

git fetch origin "$BRANCH" -q 2>/dev/null || exit 0

LOCAL_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo "")"
REMOTE_COMMIT="$(git rev-parse "origin/$BRANCH" 2>/dev/null || echo "")"

if [ -n "$LOCAL_COMMIT" ] && [ -n "$REMOTE_COMMIT" ] && [ "$LOCAL_COMMIT" != "$REMOTE_COMMIT" ]; then
    echo "============================================================"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] New commit detected on GitHub!"
    echo "Current: ${LOCAL_COMMIT:0:7} -> New: ${REMOTE_COMMIT:0:7}"
    echo "Running update.sh..."
    echo "============================================================"
    bash "$APP_DIR/update.sh"
fi
