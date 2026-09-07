# `app/core/` — cross-cutting plumbing

The bottom of the stack. Six files, no shared theme except that **every other
layer needs them and none of them needs another layer**.

`core/` imports nothing from `app/` (only from itself). That is deliberate: it
means importing `core` can never create a circular import, which is why
`settings` and `get_logger` are safe to reach for from literally anywhere.

```
   models/   schemas/   services/   api/   simulation/   optimization/
      │          │          │        │          │             │
      └──────────┴──────────┴────┬───┴──────────┴─────────────┘
                                 │  everyone imports core
                                 ▼
                            app/core/
                                 │  core imports nobody
                                 ▼
                        (stdlib + third-party only)
```

---

## File index

| File | Owns | Used by |
|---|---|---|
| `config.py` | Every tunable setting, loaded from env vars | everyone |
| `logging.py` | JSON log formatter + the request-id context | `main.py`, services |
| `errors.py` | `APIError` and the handlers that shape error responses | `main.py`, services |
| `security.py` | Password hashing (bcrypt) and JWT encode/decode | `api/dependencies/auth.py`, `api/routes/auth.py` |
| `redis.py` | The Redis client and small JSON cache helpers | dashboard + analytics services |
| `metrics.py` | Prometheus counters, histograms and gauges | `main.py` middleware |

---

## `config.py` — one object, read once

Every setting lives on one `Settings` class, filled from environment variables
(or a `.env` file), and is exposed as a module-level `settings` singleton:

```python
from app.core.config import settings
settings.solver_time_limit_seconds   # -> 15
```

`get_settings()` is wrapped in `@lru_cache`, so the environment is parsed exactly
once per process and every importer sees the same object.

**Two details worth knowing, because both fix real deployment bugs:**

1. **Database URLs are normalised on load.** Managed hosts (Render, Railway, Fly)
   hand out one `DATABASE_URL` like `postgres://…`. The app needs an *async*
   driver and Alembic needs a *sync* one, so a validator derives both:

   ```
   DATABASE_URL=postgres://...
             │
             ├──> database_url      = postgresql+asyncpg://...   (the app)
             └──> database_url_sync = postgresql+psycopg://...    (Alembic)
   ```

2. **CORS origins are a comma-separated string, not a list.** Declaring
   `list[str]` would make pydantic-settings try to JSON-decode the env value
   before any validator runs, and fail. So the raw value is a string and the
   `cors_origins` property does the splitting — and also expands a bare hostname
   (which some hosts inject) into a full origin, since CORS matching is exact.

## `logging.py` — structured logs and request tracing

Logs come out as one JSON object per line, so a log aggregator can filter on
fields instead of grepping text:

```json
{"ts":"2026-01-14T10:22:31","level":"INFO","logger":"routeos",
 "request_id":"a3f9c1d20b74","message":"request",
 "method":"POST","path":"/api/v1/orders","status_code":201,"latency_ms":42.7}
```

The interesting piece is `request_id_ctx`, a `ContextVar`. The middleware in
`main.py` sets a fresh id at the start of each request, and `JsonFormatter` reads
it back when formatting *any* log line during that request:

```
  request arrives ──> request_id_ctx.set("a3f9c1d2")
                          │
                          │  a service logs something, deep in the call stack,
                          │  without being passed the id
                          ▼
                      JsonFormatter reads the ContextVar
                          │
                          ▼
                      every line carries request_id=a3f9c1d2
```

A `ContextVar` (rather than a global) is what makes this correct under async:
each concurrent request gets its own value, so ids never bleed between requests
being served at the same time.

## `errors.py` — every failure looks the same

Any error the API returns has one shape, so a client needs only one parser:

```json
{"error": {"code": "ORDER_IMMUTABLE", "message": "…", "details": {}}}
```

Three handlers are registered onto the app, each translating a different failure
into that envelope:

| Raised | Handler produces | Status |
|---|---|---|
| `APIError("CODE", "msg", 409)` | that code and message | whatever was passed |
| `RequestValidationError` (bad body) | `VALIDATION_ERROR` + the field errors | 422 |
| `StarletteHTTPException` (404, …) | `HTTP_ERROR` | as raised |

Services raise `APIError` (or the `not_found()` shorthand) and never build a
response themselves — that's what keeps business logic free of HTTP details.

## `security.py` — passwords and tokens

Two unrelated jobs, both cryptographic:

- **Passwords** are hashed with bcrypt via passlib. Only `password_hash` is ever
  stored; a plaintext password exists just long enough to hash or verify.
- **Tokens** are signed JWTs carrying `sub` (the user id), `role`, `iat` and
  `exp`. Putting the role *in* the token means a permission check needs no
  database round-trip for the role itself.

## `redis.py` — cache that is allowed to fail

Redis serves three purposes: caching expensive aggregations, holding hot
simulation state, and fanning out events.

The important design choice is that **every helper swallows its exceptions**:

```python
async def cache_get_json(key):
    try:
        ...
    except Exception:
        return None      # a cache miss, not an error
```

That looks careless but is intentional and commented as such: a cache is an
optimisation. If Redis is down, a cache read should degrade into "recompute it",
never into a 500 for the user. The database stays the source of truth.

## `metrics.py` — what Prometheus scrapes

Five metrics, exposed at `/metrics`:

| Metric | Type | Meaning |
|---|---|---|
| `routeos_http_requests_total` | Counter | requests, by method/path/status |
| `routeos_http_request_latency_seconds` | Histogram | latency distribution |
| `routeos_optimization_duration_seconds` | Histogram | solver wall time |
| `routeos_active_simulations` | Gauge | vehicles currently moving |
| `routeos_websocket_connections` | Gauge | open sockets |

Counters and histograms are updated as things happen (in the middleware). The two
gauges are instead sampled *at scrape time* in `main.py`, because they describe a
current value rather than an accumulating total.
