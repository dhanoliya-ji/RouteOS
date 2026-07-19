# 🌐 Deploy RouteOS to a free public URL (Render)

This gives you a shareable live demo without paying. It uses [`render.yaml`](render.yaml) to
provision **Postgres (PostGIS) + Redis + backend API + frontend** on Render's free tier.

> Free web services **sleep after ~15 min idle** and cold-start in ~50s. Perfect for a portfolio
> demo; not for production traffic.

## Steps

1. **Push this repo to GitHub** (public or private — Render can access both once authorized).
2. Go to **https://render.com** → sign up (free) → **New ➜ Blueprint**.
3. **Connect the repo.** Render detects [`render.yaml`](render.yaml) and lists 4 resources:
   `routeos-db`, `routeos-redis`, `routeos-backend`, `routeos-frontend`.
4. Click **Apply**. Render builds and deploys everything (first deploy ~5–8 min).
5. Open the **frontend** URL Render gives you (e.g. `https://routeos-frontend.onrender.com`).
   Log in with `dispatcher@routeos.dev` / `dispatch12345`.

The backend's Docker entrypoint runs migrations and seeds demo data automatically on first boot,
just like locally.

## The one thing to check

The frontend is built with the backend's URL baked in. The blueprint assumes:

```
VITE_API_BASE_URL = https://routeos-backend.onrender.com
VITE_WS_BASE_URL  = wss://routeos-backend.onrender.com
```

If Render appends a random suffix to your backend service name (it does this only if the name is
already taken globally), update those two env vars on the **routeos-frontend** service to match your
actual backend URL, then **Manual Deploy ➜ Clear build cache & deploy** the frontend.

Similarly set the backend's `BACKEND_CORS_ORIGINS` to your actual frontend URL if it differs.

## Notes & gotchas

- **PostGIS**: created automatically by the first Alembic migration (`CREATE EXTENSION postgis`).
  PostGIS is on Render's allowed-extensions list, so no superuser action is needed.
- **Database URL**: Render hands out a standard `postgresql://…` string. The backend normalizes it
  to the async (`+asyncpg`) and sync (`+psycopg`) drivers automatically — you don't set anything.
- **Cold starts**: the very first request after idle wakes the API; give it ~50s.
- **Alternatives**: the same four services deploy just as well on **Railway** or **Fly.io** — map
  `DATABASE_URL`, `REDIS_URL`, and the two `VITE_*` build vars to their equivalents.

## Prefer zero cloud setup?

For most reviewers, the simplest path is still local — one command, everything included:

```bash
cp .env.example .env
docker compose up --build
# → http://localhost:5173
```

See the [README](README.md) for the full local guide and demo walkthrough.
