# `app/db/` — the database connection

Two small files that answer two questions: **what shape do our tables take**
(`base.py`) and **how do we get a connection to run queries on** (`session.py`).

No table definitions live here — those are in [`../models/`](../models/). This
folder is only the machinery underneath them.

```
      models/*.py                     services/, api/
     "here is a table"              "I need to run a query"
           │                                  │
           ▼                                  ▼
      db/base.py                       db/session.py
      Base, TimestampMixin             engine + AsyncSessionLocal
           │                                  │
           └───────────────┬──────────────────┘
                           ▼
                  PostgreSQL + PostGIS
```

---

## File index

| File | Contents |
|---|---|
| `base.py` | `Base` (the declarative base every model inherits), `TimestampMixin`, and `import_models()` |
| `session.py` | `engine` (the connection pool), `AsyncSessionLocal` (session factory), `get_db()` (the FastAPI dependency) |

---

## `base.py`

**`Base`** is the declarative base. Every model subclasses it, and that
subclassing is what registers a table on `Base.metadata` — the single registry
Alembic reads to work out what the schema should look like.

**`TimestampMixin`** adds one column, `created_at`, filled by the *database*:

```python
server_default=func.now()
```

Not `default=datetime.now()`. The difference matters: `server_default` compiles
into the table definition so Postgres stamps the row, meaning the clock is the
database's. With a Python default, a server whose clock has drifted would write a
misleading timestamp, and rows inserted by a migration or by hand would get none
at all.

Models pick it up by multiple inheritance:

```python
class Order(Base, TimestampMixin):   # a table, and it has created_at
```

**`import_models()`** exists for one reason. `Base.metadata` only knows about a
table once the module defining it has been *imported* — and Alembic's autogenerate
starts from `metadata`, not from your source tree. So Alembic calls this function
to force every model module to load; without it, autogenerate would cheerfully
produce a migration that drops the tables it "can't see".

---

## `session.py`

### The engine is a connection pool

```python
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)
```

Opening a TCP connection to Postgres and authenticating costs milliseconds, which
is a lot to pay per request. So the engine keeps connections open and lends them
out:

```
   pool_size=10        10 connections kept open permanently
   max_overflow=20     up to 20 more opened under load, then discarded
                       ──> hard ceiling of 30 concurrent connections

   pool_pre_ping=True  before lending a connection out, send a cheap ping.
                       A pooled connection can die while idle (Postgres
                       restart, a network device dropping the socket). The
                       ping costs a round-trip and turns a mystery
                       "connection closed" error into a transparent
                       reconnect.
```

The engine is created **once** at import time and shared. Creating one per
request would defeat the entire point of pooling.

### Two ways to get a session

There is exactly one session factory, `AsyncSessionLocal`, and two ways to use
it — which one you want depends on whether you are inside a request.

**1. Inside a request — inject `get_db`:**

```python
@router.get("/orders")
async def list_orders(db: AsyncSession = Depends(get_db)):
    ...
```

FastAPI calls `get_db()`, which yields a session and — because it is a generator
dependency — resets it when the response is done. The `try/except` rolls back on
failure so a half-finished transaction is never left behind, and the
`async with` closes the session and returns its connection to the pool.

**2. Outside a request — open one yourself:**

```python
async with AsyncSessionLocal() as db:
    ...
```

Background work has no request to hang off, so it must own its session. Two
places do this, and both *have* to:

- **`simulation/engine.py`** — the tick loop opens a fresh session each tick.
- **`services/optimization_service.py`** — a background solve outlives the
  request that started it, so by the time it finishes the request's session is
  long gone. Reusing it would fail; the job opens its own.

### `expire_on_commit=False`

The setting worth knowing about, because the default causes a specific crash here:

```python
AsyncSessionLocal = async_sessionmaker(..., expire_on_commit=False, ...)
```

By default SQLAlchemy marks every attribute stale after a commit, so the next
attribute read silently re-queries. Under asyncio that lazy re-query raises
`MissingGreenlet` instead — and the pattern it breaks is the most ordinary one
there is:

```python
await db.commit()
return order          # serialising order.id would trigger a lazy reload -> boom
```

Turning it off means committed objects keep their loaded values and stay usable
after the commit.

### Relationships still need eager loading

`expire_on_commit=False` fixes committed *columns*, not unloaded *relationships*.
Touching `route.stops` when it was never loaded is still a lazy load, and still
raises `MissingGreenlet` under async. So relationship access must be requested up
front:

```python
select(Route).options(selectinload(Route.stops))
```

`services/route_service.py` and `services/simulation_service.py` both do this.
If you ever see `MissingGreenlet`, a missing `selectinload` is the first thing to
look for.
