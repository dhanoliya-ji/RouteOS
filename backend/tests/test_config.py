"""Configuration normalisation tests (no database).

These cover the wiring that managed hosts depend on: a single standard
``DATABASE_URL`` must yield both driver URLs, and CORS entries injected as bare
hostnames must be expanded into real origins (browsers match origins exactly).
"""
from __future__ import annotations

from app.core.config import Settings


def _settings(**overrides) -> Settings:
    # Ignore any ambient .env so the assertions below are deterministic.
    return Settings(_env_file=None, **overrides)


class TestCorsOrigins:
    def test_bare_remote_host_becomes_https_origin(self):
        s = _settings(backend_cors_origins="routeos-frontend.onrender.com")
        assert s.cors_origins == ["https://routeos-frontend.onrender.com"]

    def test_bare_localhost_becomes_http_origin(self):
        s = _settings(backend_cors_origins="localhost:5173,127.0.0.1:3000")
        assert s.cors_origins == ["http://localhost:5173", "http://127.0.0.1:3000"]

    def test_explicit_scheme_is_preserved(self):
        s = _settings(backend_cors_origins="http://localhost:5173,https://app.example.com")
        assert s.cors_origins == ["http://localhost:5173", "https://app.example.com"]

    def test_trailing_slash_and_blanks_are_stripped(self):
        s = _settings(backend_cors_origins="https://x.dev/, ,https://y.dev")
        assert s.cors_origins == ["https://x.dev", "https://y.dev"]


class TestDatabaseUrlNormalisation:
    def test_managed_postgres_url_yields_both_drivers(self):
        # Render/Heroku-style URL, exactly as those platforms hand it out.
        s = _settings(database_url="postgres://u:p@host:5432/db")
        assert s.database_url == "postgresql+asyncpg://u:p@host:5432/db"
        assert s.database_url_sync == "postgresql+psycopg://u:p@host:5432/db"

    def test_plain_postgresql_url_is_upgraded(self):
        s = _settings(database_url="postgresql://u:p@host/db")
        assert s.database_url.startswith("postgresql+asyncpg://")
        assert s.database_url_sync.startswith("postgresql+psycopg://")

    def test_already_async_url_is_left_alone(self):
        s = _settings(database_url="postgresql+asyncpg://u:p@host/db")
        assert s.database_url == "postgresql+asyncpg://u:p@host/db"
        assert s.database_url_sync == "postgresql+psycopg://u:p@host/db"
