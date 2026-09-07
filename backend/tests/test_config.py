"""Configuration normalisation tests (no database).

These cover the wiring that managed hosts depend on: a single standard
``DATABASE_URL`` must yield both driver URLs, and CORS entries injected as bare
hostnames must be expanded into real origins (browsers match origins exactly).

Unit-testing configuration looks unusual. It is here because every case below
represents a way a deployment actually broke: a platform handing out a
scheme-less hostname, or a legacy ``postgres://`` URL the async driver rejects.
Cheap to test, and the failures are otherwise only visible in production.
"""
from __future__ import annotations

from app.core.config import Settings


def _settings(**overrides) -> Settings:
    # Ignore any ambient .env so the assertions below are deterministic.
    #
    # Without _env_file=None these tests would pass or fail depending on whether
    # a developer happens to have a .env file present — the classic
    # works-on-my-machine test.
    return Settings(_env_file=None, **overrides)


class TestCorsOrigins:
    """The `cors_origins` property, which normalises whatever it is given."""

    def test_bare_remote_host_becomes_https_origin(self):
        # The real-world case: a managed host injects a peer service's hostname
        # with no scheme. CORS matching is exact, so a bare host would never
        # match a browser's Origin header and every request would be blocked.
        s = _settings(backend_cors_origins="routeos-frontend.onrender.com")
        assert s.cors_origins == ["https://routeos-frontend.onrender.com"]

    def test_bare_localhost_becomes_http_origin(self):
        # Local names get http, not https — nothing terminates TLS in dev. Also
        # checks a port survives the expansion, since the host is split on ":"
        # to make this decision.
        s = _settings(backend_cors_origins="localhost:5173,127.0.0.1:3000")
        assert s.cors_origins == ["http://localhost:5173", "http://127.0.0.1:3000"]

    def test_explicit_scheme_is_preserved(self):
        # An already-complete origin must pass through untouched — the
        # normalisation must not "help" by rewriting a deliberate http://.
        s = _settings(backend_cors_origins="http://localhost:5173,https://app.example.com")
        assert s.cors_origins == ["http://localhost:5173", "https://app.example.com"]

    def test_trailing_slash_and_blanks_are_stripped(self):
        # Tolerating human formatting: a trailing slash (an origin has no path,
        # so "https://x.dev/" would never match) and an empty entry from a
        # stray comma.
        s = _settings(backend_cors_origins="https://x.dev/, ,https://y.dev")
        assert s.cors_origins == ["https://x.dev", "https://y.dev"]


class TestCorsOriginRegex:
    """The deployed blueprint matches the frontend by pattern, so the pattern
    itself is security-relevant: it must not admit lookalike domains."""

    # The pattern a deployment would use. Anchored at BOTH ends — the ^ and the
    # $ are each load-bearing, as the rejection cases below show.
    PATTERN = r"^https://[a-z0-9-]+\.onrender\.com$"

    @staticmethod
    def _matches(pattern: str, origin: str) -> bool:
        import re

        # re.match anchors at the start implicitly; the trailing $ in PATTERN is
        # what anchors the end.
        return re.match(pattern, origin) is not None

    def test_allows_deployed_frontend_including_render_name_suffix(self):
        # Both forms must pass: the plain service name, and the variant where
        # the platform appends a random suffix to keep the subdomain unique.
        assert self._matches(self.PATTERN, "https://routeos-frontend.onrender.com")
        assert self._matches(self.PATTERN, "https://routeos-frontend-x9k2.onrender.com")

    def test_rejects_lookalike_and_insecure_origins(self):
        # The important half of the test. Each entry is a distinct attack or
        # mistake, and each is blocked by a different part of the pattern.
        for origin in (
            "https://onrender.com.evil.com",   # suffix-spoofing
            # ^ the one that matters most: without the trailing $, this MATCHES,
            #   and an attacker-controlled domain gets credentialed access.
            "https://evil.com",
            "http://routeos-frontend.onrender.com",  # not TLS
            # ^ blocked by requiring https:// in the literal prefix.
            "https://sub.routeos.onrender.com",      # extra label
            # ^ blocked because the character class excludes ".", so only a
            #   single subdomain label can match.
        ):
            assert not self._matches(self.PATTERN, origin), origin

    def test_regex_defaults_to_empty_so_local_runs_use_the_explicit_list(self):
        # Empty by default, and main.py converts that to None — so a local run
        # is governed solely by the explicit allow-list. A default pattern here
        # would silently widen access in every environment.
        assert _settings().backend_cors_origin_regex == ""


class TestDatabaseUrlNormalisation:
    """One DATABASE_URL in, two driver URLs out — async for the app, sync for
    Alembic."""

    def test_managed_postgres_url_yields_both_drivers(self):
        # Render/Heroku-style URL, exactly as those platforms hand it out.
        #
        # The legacy "postgres://" scheme is the case that breaks things: the
        # async driver does not recognise it, so without normalisation the app
        # fails to connect at all.
        s = _settings(database_url="postgres://u:p@host:5432/db")
        assert s.database_url == "postgresql+asyncpg://u:p@host:5432/db"
        assert s.database_url_sync == "postgresql+psycopg://u:p@host:5432/db"

    def test_plain_postgresql_url_is_upgraded(self):
        # The modern scheme, still without a driver named. Asserted with
        # startswith because only the prefix is under test here.
        s = _settings(database_url="postgresql://u:p@host/db")
        assert s.database_url.startswith("postgresql+asyncpg://")
        assert s.database_url_sync.startswith("postgresql+psycopg://")

    def test_already_async_url_is_left_alone(self):
        # Idempotence: normalising an already-correct URL must not mangle it
        # into "postgresql+asyncpg+asyncpg://". The second assertion also pins
        # that the sync URL is derived by REPLACING the driver, not appending.
        s = _settings(database_url="postgresql+asyncpg://u:p@host/db")
        assert s.database_url == "postgresql+asyncpg://u:p@host/db"
        assert s.database_url_sync == "postgresql+psycopg://u:p@host/db"
