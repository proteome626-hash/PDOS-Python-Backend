#!/usr/bin/env bash
# ──────────────────────────────────────────────
# PDOS Backend — Convenience Deploy Wrapper
# ──────────────────────────────────────────────
# Copies the deploy/ folder to a remote server and runs setup.sh.
#
# Usage:
#   export REPO_URL=https://github.com/your-org/pdos-backend.git
#   ./deploy/deploy.sh user@your-server-ip
#
# Flags:
#   --seed   Include demo data seeding
# ──────────────────────────────────────────────
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 [--seed] user@hostname"
  echo "  --seed   Seed demo data after setup"
  exit 1
fi

SEED=""
if [ "$1" = "--seed" ]; then
  SEED="--seed"
  shift
fi

TARGET="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "──> Copying deploy/ to $TARGET:/tmp/pdos-deploy ..."
scp -r "$SCRIPT_DIR" "$TARGET:/tmp/pdos-deploy"

echo "──> Running setup.sh on $TARGET ..."
ssh -t "$TARGET" "sudo bash /tmp/pdos-deploy/setup.sh $SEED; rm -rf /tmp/pdos-deploy"
