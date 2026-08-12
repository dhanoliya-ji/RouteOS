"""Central application configuration, loaded from environment variables."""
from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _to_async_url(url: str) -> str:
    """Coerce a plain postgres URL (e.g. what managed hosts hand out) to asyncpg."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


def _to_sync_url(url: str) -> str:
    """Derive the psycopg (sync) URL used by Alembic from any postgres URL."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # App
    environment: str = "development"
    log_level: str = "INFO"
    project_name: str = "RouteOS"
    api_v1_prefix: str = "/api/v1"

    # Database
    database_url: str = "postgresql+asyncpg://routeos:routeos_dev_password@localhost:5432/routeos"
    database_url_sync: str = "postgresql+psycopg://routeos:routeos_dev_password@localhost:5432/routeos"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Security
    secret_key: str = "change-me-in-production"
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
            origin = raw.strip().rstrip("/")
            if not origin:
                continue
            if "://" in origin:
                origins.append(origin)
                continue
            # Bare host: local names stay http, anything else is TLS-terminated.
            host_only = origin.split(":")[0]
            if host_only in ("localhost", "127.0.0.1"):
                origins.append(f"http://{origin}")
            else:
                origins.append(f"https://{origin}")
        return origins

    # Optimization / routing
    osrm_base_url: str = "https://router.project-osrm.org"
    use_osrm: bool = False
    road_distance_factor: float = 1.25
    average_speed_kmh: float = 30.0
    solver_time_limit_seconds: int = 15
    # OR-Tools FirstSolutionStrategy name. The starting solution dominates the
    # result whenever the local search gets few iterations (small CPU budgets),
    # so this is worth tuning per deployment.
    solver_first_solution_strategy: str = "PATH_CHEAPEST_ARC"

    # Demo accounts
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
        self.database_url = _to_async_url(self.database_url)
        self.database_url_sync = _to_sync_url(self.database_url)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
