#!/usr/bin/env bash
set -e

echo "[entrypoint] Waiting for Postgres at ${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5432}..."
python - <<'PY'
import os, time, socket
host = os.getenv("POSTGRES_HOST", "postgres")
port = int(os.getenv("POSTGRES_PORT", "5432"))
for _ in range(60):
    try:
        with socket.create_connection((host, port), timeout=2):
            print("[entrypoint] Postgres is up.")
            break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit("[entrypoint] Postgres never became reachable.")
PY

echo "[entrypoint] Running Alembic migrations..."
alembic upgrade head

echo "[entrypoint] Seeding demo data (idempotent)..."
python -m scripts.seed_data || echo "[entrypoint] Seed skipped/failed (non-fatal)."

echo "[entrypoint] Starting: $@"
exec "$@"
