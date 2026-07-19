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

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]

    # Optimization / routing
    osrm_base_url: str = "https://router.project-osrm.org"
    use_osrm: bool = False
    road_distance_factor: float = 1.25
    average_speed_kmh: float = 30.0
    solver_time_limit_seconds: int = 15

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
