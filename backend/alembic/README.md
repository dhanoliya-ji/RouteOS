# `backend/alembic/` — database migrations

How the database schema gets created and changed. Alembic keeps an ordered chain
of migration scripts and a marker in the database saying which one has been
applied, so any database can be brought to the current schema by replaying
whatever it is missing.

```
   alembic/
   ├── env.py             configuration — runs before any migration
   ├── script.py.mako     template for newly generated migrations
   └── versions/
       └── 0001_initial.py   the whole schema + PostGIS
```

---

## When migrations run

You rarely run them by hand. `docker-entrypoint.sh` does it on every container
start, *before* uvicorn:

```
   container starts
        │
        ▼
   wait for Postgres to accept TCP
        │
        ▼
   alembic upgrade head        <- here
        │
        ▼
   python -m scripts.seed_data   (non-fatal if it fails)
        │
        ▼
   exec uvicorn
```

`upgrade head` is a no-op on an already-current database, which is what makes it
safe to run on every boot.

---

## `env.py` — two things worth knowing

**1. The database URL is not in `alembic.ini`.**

```ini
# alembic.ini
sqlalchemy.url =          # deliberately blank
```

```python
# env.py
config.set_main_option("sqlalchemy.url", settings.database_url_sync)
```

The URL comes from `Settings` at runtime instead. That keeps **one** source of
truth: change `DATABASE_URL` in the environment and both the app and the
migrations follow. Committing a URL to `alembic.ini` would also mean committing
a password.

Note it uses `database_url_sync`, not `database_url`. Alembic is synchronous, so
it needs the `psycopg` driver while the app uses `asyncpg`. `core/config.py`
derives both from whatever single URL it was given — see
[`../app/core/README.md`](../app/core/README.md).

**2. `import app.models` is load-bearing.**

```python
import app.models  # noqa: F401
```

It looks like an unused import, and the `noqa` is there because linters agree.
But `target_metadata = Base.metadata` is only populated by the *side effect* of
importing the model modules. Without this line `Base.metadata` would be empty,
and autogenerate would confidently produce a migration that drops every table.

**3. `poolclass=pool.NullPool`.** A migration run is a short-lived process that
needs one connection and then exits; pooling would just leave connections to
clean up.

---

## The initial migration is unusual

`0001_initial.py` does not contain a list of `op.create_table()` calls. It builds
the schema straight from the ORM metadata:

```python
def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    Base.metadata.create_all(bind=bind)
```

**Why `CREATE EXTENSION` comes first:** the `depots` and `orders` tables have
`GEOGRAPHY` columns, and that type does not exist in a stock Postgres database —
it is provided by PostGIS. Creating the tables before enabling the extension
fails with `type "geography" does not exist`. Order matters.

**Why `create_all` instead of explicit DDL:** for the first migration of a
project the two are equivalent, and generating from metadata cannot drift from
the models. GeoAlchemy2 also registers DDL hooks that fire during
`create_all`, which is what creates the **GiST** spatial indexes on the
geography columns — those never appear in the migration source at all.

The trade-off is real: `create_all` is not a substitute for hand-written
migrations. It creates whatever the models currently say, so it is only
appropriate for the initial revision.

---

## Adding a migration

From `backend/`, with the database running:

```bash
# 1. change a model in app/models/
# 2. generate a migration by diffing models against the live schema
alembic revision --autogenerate -m "add cold_chain flag to orders"

# 3. READ the generated file. Always.
# 4. apply it
alembic upgrade head
```

Step 3 is not optional. Autogenerate is a diffing tool, not an oracle, and it is
reliably wrong about a few things:

| It handles | It does not |
|---|---|
| new/dropped tables and columns | a rename — it emits a drop plus an add, **losing the data** |
| type and nullability changes | data backfills of any kind |
| index and unique-constraint changes | anything requiring a specific order of operations |
| foreign keys | server defaults, unevenly |

For a rename you replace the drop/add pair with `op.alter_column(...,
new_column_name=...)` by hand.

Because subsequent migrations will be normal `op.*` scripts while `0001` is a
`create_all`, note that autogenerate compares models to the *database*, not to
the migration history — so it works correctly on top of `0001` regardless.

---

## Everyday commands

| Command | Does |
|---|---|
| `alembic current` | which revision this database is on |
| `alembic history --verbose` | the full chain |
| `alembic upgrade head` | apply everything outstanding |
| `alembic downgrade -1` | undo the last one |
| `alembic upgrade +1` | step forward one |

Inside Docker: `docker compose exec backend alembic current`.

---

## `downgrade()` here is destructive

```python
def downgrade():
    Base.metadata.drop_all(bind=bind)
```

For `0001` that means **dropping every table and all its data** — correct for
"undo the initial migration", since the only prior state is an empty database.
But it is not a rollback you ever want to run against a database holding
anything you care about.

The PostGIS extension is deliberately *not* dropped: other schemas in the same
database may depend on it, and removing a shared extension on the way out would
be an unpleasant surprise.
