#!/usr/bin/env bash
set -e

# Wait for Postgres to accept connections.
#
# Managed hosts (Render, Railway, Fly) hand out a single DATABASE_URL and never
# set POSTGRES_HOST/POSTGRES_PORT, so the connection target is parsed out of
# DATABASE_URL first and the discrete vars are only a fallback (docker-compose
# sets those). Defaulting blindly to "postgres:5432" made the container spin for
# 60s against an unresolvable hostname and then exit, failing the deploy.
python - <<'PY'
import os
import socket
import time
from urllib.parse import urlsplit

url = os.getenv("DATABASE_URL", "")
host, port = None, None

if url:
    # SQLAlchemy-style drivers (postgresql+asyncpg://) confuse urlsplit's port
    # parsing on some versions, so normalise the scheme before splitting.
    scheme, _, rest = url.partition("://")
    parts = urlsplit(f"//{rest}" if rest else url)
    try:
        host, port = parts.hostname, parts.port
    except ValueError:
        host, port = None, None

host = host or os.getenv("POSTGRES_HOST", "postgres")
port = int(port or os.getenv("POSTGRES_PORT", "5432"))

print(f"[entrypoint] Waiting for Postgres at {host}:{port}...", flush=True)
deadline = time.time() + 120
last_error = None
while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=5):
            print("[entrypoint] Postgres is up.", flush=True)
            break
    except OSError as exc:
        last_error = exc
        time.sleep(2)
else:
    raise SystemExit(f"[entrypoint] Postgres at {host}:{port} never became reachable: {last_error}")
PY

echo "[entrypoint] Running Alembic migrations..."
alembic upgrade head

echo "[entrypoint] Seeding demo data (idempotent)..."
python -m scripts.seed_data || echo "[entrypoint] Seed skipped/failed (non-fatal)."

echo "[entrypoint] Starting: $@"
exec "$@"
