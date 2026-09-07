"""Central application configuration, loaded from environment variables.

Everything tunable in RouteOS lives on the single ``Settings`` class below, and
is reached through the module-level ``settings`` object:

    from app.core.config import settings
    settings.solver_time_limit_seconds

Where values come from, highest priority first:

    1. a real environment variable          (how production is configured)
    2. a .env file next to the backend      (how local dev is configured)
    3. the default written on the field     (so the app runs with no setup)

Field names map to env vars case-insensitively: ``database_url`` is read from
``DATABASE_URL``.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _to_async_url(url: str) -> str:
    """Coerce a plain postgres URL (e.g. what managed hosts hand out) to asyncpg.

    The app talks to Postgres asynchronously, which requires the driver to be
    named in the URL scheme. Managed hosts don't know that, so we upgrade
    whatever they gave us:

        postgres://u:p@h/db            -> postgresql+asyncpg://u:p@h/db
        postgresql://u:p@h/db          -> postgresql+asyncpg://u:p@h/db
        postgresql+asyncpg://u:p@h/db  -> unchanged (already correct)
    """
    # "postgres://" is the legacy form Heroku popularised; normalise it first so
    # the next branch only has one shape to think about.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


def _to_sync_url(url: str) -> str:
    """Derive the psycopg (sync) URL used by Alembic from any postgres URL.

    Alembic is synchronous, so it cannot use the asyncpg driver the app uses.
    Rather than ask for the same database twice in two formats, we accept one
    URL and rewrite the scheme. Whichever of the three driver prefixes is
    present gets replaced with psycopg.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    # Not a postgres URL we recognise — hand it back untouched rather than
    # corrupting it. Alembic will fail with a clear driver error instead.
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Two candidate .env locations: running from backend/, or from the repo
        # root. First match wins.
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        # Ignore unknown env vars instead of erroring. The shared .env also
        # holds frontend and docker-compose keys that mean nothing here.
        extra="ignore",
    )

    # --- App identity & logging ---------------------------------------------
    environment: str = "development"
    log_level: str = "INFO"
    project_name: str = "RouteOS"
    api_v1_prefix: str = "/api/v1"

    # --- Database ------------------------------------------------------------
    # Both are normalised by the validator at the bottom of this class, so you
    # only ever need to set DATABASE_URL. The sync variant is for Alembic.
    database_url: str = "postgresql+asyncpg://routeos:routeos_dev_password@localhost:5432/routeos"
    database_url_sync: str = "postgresql+psycopg://routeos:routeos_dev_password@localhost:5432/routeos"

    # --- Redis ---------------------------------------------------------------
    # Used for cached aggregations only; the app runs (slower) without it.
    redis_url: str = "redis://localhost:6379/0"

    # --- Security ------------------------------------------------------------
    # MUST be overridden in production: it signs every JWT, so anyone who knows
    # it can mint a valid admin token.
    secret_key: str = "change-me-in-production"
    # 24 hours. Long-lived tokens are why get_current_user re-reads the user on
    # every request — that is_active check is the only revocation there is.
    access_token_expire_minutes: int = 1440
    algorithm: str = "HS256"

    # CORS — stored as a comma-separated string (pydantic-settings would try to
    # JSON-decode a list[str] env value before validators run). Use `cors_origins`.
    backend_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Optional regex of allowed origins, used instead of naming the frontend
    # explicitly. A blueprint cannot have the backend reference the frontend
    # while the frontend references the backend — managed hosts reject the
    # circular dependency — so on those platforms the deployed frontend is
    # matched by pattern instead. Starlette echoes the matched origin back
    # (never a wildcard), so credentialed requests stay spec-compliant.
    #
    # Security note: anchor the pattern at BOTH ends. "^https://.*onrender\.com"
    # without a trailing $ also matches https://onrender.com.evil.com.
    backend_cors_origin_regex: str = ""

    @property
    def cors_origins(self) -> list[str]:
        """Allowed origins, normalised to full origins.

        Managed hosts (Render et al.) can inject a peer service's *bare hostname*
        with no scheme. CORS matching is exact, so a bare host would never match
        a browser's `Origin` header — expand it to a proper origin here.
        """
        origins: list[str] = []
        for raw in self.backend_cors_origins.split(","):
            # Tolerate the formatting a human or a platform might produce:
            # stray spaces, a trailing slash (an origin has no path).
            origin = raw.strip().rstrip("/")
            if not origin:
                continue  # empty entry from a trailing comma
            if "://" in origin:
                origins.append(origin)  # already a full origin
                continue
            # Bare host: local names stay http, anything else is TLS-terminated.
            # Strip any port before testing, so "localhost:5173" is recognised.
            host_only = origin.split(":")[0]
            if host_only in ("localhost", "127.0.0.1"):
                origins.append(f"http://{origin}")
            else:
                origins.append(f"https://{origin}")
        return origins

    # --- Optimization / routing ---------------------------------------------
    osrm_base_url: str = "https://router.project-osrm.org"
    # Off by default: the public demo server is rate-limited and may be down.
    # Off means haversine * road_distance_factor, which always works.
    use_osrm: bool = False
    # Roads are not straight lines. Multiply crow-flies distance by this to
    # approximate real driving distance in a city.
    road_distance_factor: float = 1.25
    # Used to turn a distance into a duration. One flat figure — no traffic
    # model, no road classes.
    average_speed_kmh: float = 30.0
    # Budget for a synchronous POST /optimization/run, where a client is
    # holding the connection open. Kept small for that reason.
    solver_time_limit_seconds: int = 15
    # Budget for background (job) runs. These are not bound to an HTTP request,
    # so the solver can search far longer — which is what lets a CPU-starved
    # instance still reach a plan that beats the greedy baseline.
    solver_async_time_limit_seconds: int = 180
    # Background budget is scaled to the order count between these bounds, so a
    # small run does not sit waiting after its search has already converged.
    solver_min_async_time_limit_seconds: int = 30
    solver_seconds_per_order: float = 1.6
    # Concurrent background solves allowed. Each pins a CPU for minutes, so this
    # protects a small instance from being swamped by repeated requests.
    max_concurrent_optimization_jobs: int = 2
    # OR-Tools FirstSolutionStrategy name. The starting solution dominates the
    # result whenever the local search gets few iterations (small CPU budgets),
    # so this is worth tuning per deployment.
    solver_first_solution_strategy: str = "PATH_CHEAPEST_ARC"

    # --- Demo accounts -------------------------------------------------------
    # Created by scripts/seed_data.py. Demo credentials only — anything
    # internet-facing must override these.
    demo_admin_email: str = "admin@routeos.dev"
    demo_admin_password: str = "admin12345"
    demo_dispatcher_email: str = "dispatcher@routeos.dev"
    demo_dispatcher_password: str = "dispatch12345"
    demo_viewer_email: str = "viewer@routeos.dev"
    demo_viewer_password: str = "viewer12345"

    @model_validator(mode="after")
    def _normalize_db_urls(self) -> "Settings":
        # Accept a single standard DATABASE_URL (as managed hosts like Render/Railway
        # provide) and derive both the async (app) and sync (Alembic) driver URLs.
        #
        # mode="after" so both fields are already populated from the environment.
        # Note the sync URL is derived from the *normalised* async one, so a
        # DATABASE_URL_SYNC set by hand is intentionally overridden — one input
        # cannot disagree with itself.
        self.database_url = _to_async_url(self.database_url)
        self.database_url_sync = _to_sync_url(self.database_url)
        return self


@lru_cache
def get_settings() -> Settings:
    """Build Settings once and cache it.

    Reading the environment and validating is not free, and more importantly
    every caller must see the *same* object — so a value read at import time in
    one module cannot differ from the same value elsewhere.
    """
    return Settings()


# The singleton every module imports. Created at import time, so a broken
# configuration fails immediately at startup rather than on the first request
# that happens to need the bad value.
settings = get_settings()
