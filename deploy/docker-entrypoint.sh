#!/usr/bin/env bash
set -euo pipefail

# Wait for PostgreSQL
if [ -n "${DATABASE_URL:-}" ]; then
    echo "Waiting for PostgreSQL..."
    DB_HOST=$(echo "$DATABASE_URL" | sed -n 's|.*@\([^:]*\).*|\1|p')
    DB_PORT=$(echo "$DATABASE_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
    DB_PORT="${DB_PORT:-5432}"
    until pg_isready -h "$DB_HOST" -p "$DB_PORT" 2>/dev/null; do
        sleep 1
    done
    echo "PostgreSQL is ready"
fi

# Initialize database schema (safe: create_all + stamp head)
echo "Initializing database schema..."
python scripts/init_prod_db.py

# Optionally seed demo data
if [ "${SEED_DB:-false}" = "true" ]; then
    echo "Seeding demo data..."
    python seed_db.py
fi

# Start uvicorn
exec uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "${UVICORN_WORKERS:-4}" \
    --proxy-headers \
    --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
    --log-level "${LOG_LEVEL:-info}"
