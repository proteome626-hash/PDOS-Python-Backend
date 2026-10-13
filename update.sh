#!/usr/bin/env bash
# Pull the latest code from GitHub and redeploy. Always backs up first.
# Usage: sudo bash update.sh
set -euo pipefail

# Wrapped in a function: bash parses it fully before running, so `git pull`
# rewriting this very file mid-run cannot corrupt execution.
main() {
  cd "$(dirname "$(readlink -f "$0")")"

  echo "==> Backup before update"
  bash backup.sh

  local old
  old="$(git rev-parse HEAD)"
  echo "==> git pull (current: ${old:0:7})"
  git pull --ff-only

  if [ "$(git rev-parse HEAD)" = "$old" ]; then
    echo "Already up to date. Nothing to do."
    return 0
  fi

  echo "==> Rebuild and restart"
  docker compose up -d --build

  echo "==> Health check"
  for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null http://127.0.0.1/; then
      docker image prune -f >/dev/null
      echo "Update OK: ${old:0:7} -> $(git rev-parse --short HEAD)"
      return 0
    fi
    sleep 2
  done

  echo "!! App not responding after update. Logs: docker compose logs --tail=100 backend"
  echo "!! Roll back code:  git checkout $old && docker compose up -d --build"
  echo "!! Latest DB backup is in ./backups (restore: gunzip -c FILE | docker compose exec -T db psql -U pdos -d pdos)"
  return 1
}

main "$@"
exit $?
