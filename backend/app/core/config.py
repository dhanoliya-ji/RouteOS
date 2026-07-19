"""Central application configuration, loaded from environment variables."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # CORS
    backend_cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

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

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, v: object) -> object:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
