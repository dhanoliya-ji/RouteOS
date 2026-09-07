# `app/` — the application package

Eleven sub-packages. This README is about **how they are allowed to depend on
each other**, because that is the one thing you cannot see by opening a file.

For what each package *does*, see its own README. For the request lifecycle,
see [`../README.md`](../README.md).

---

## The dependency graph is a DAG

Every arrow below was read out of the source, not designed on paper. There are
**no cycles**: you can sort the packages into levels where each package only
imports from levels strictly below it.

```
  LEVEL 7   main.py
            (assembles everything: middleware, routers, metrics endpoint)
                │
  LEVEL 6   api/
            (routes/ + dependencies/)
                │
  LEVEL 5   services/
            (business logic — the widest importer in the codebase)
                │
       ┌────────┼─────────────────┐
       │        │                 │
  LEVEL 4   simulation/           │
       │        │                 │
  LEVEL 3   optimization/     schemas/
       │        │                 │
  LEVEL 2   models/ ──────────────┘
       │        │
  LEVEL 1   db/         websocket/
       │        │           │
  LEVEL 0   core/      geospatial/
            (imports nothing from app, so nothing can cycle through it)
```

**The rule: a package may only import from a lower level.** That is what makes
the code navigable — to find who calls something you look *up*, to find what it
needs you look *down*, and you never go in circles.

---

## The full matrix

Row = the package doing the importing. `•` = it imports from that column.

| ↓ imports → | core | geospatial | db | websocket | models | schemas | optimization | simulation | services | api |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **core**         |    |    |    |    |    |    |    |    |    |    |
| **geospatial**   |    |    |    |    |    |    |    |    |    |    |
| **db**           | •  |    |    |    |    |    |    |    |    |    |
| **websocket**    | •  |    |    |    |    |    |    |    |    |    |
| **models**       |    |    | •  |    |    |    |    |    |    |    |
| **schemas**      |    |    |    |    | •  |    |    |    |    |    |
| **optimization** | •  | •  |    |    | •  |    |    |    |    |    |
| **simulation**   | •  | •  | •  | •  | •  |    |    |    |    |    |
| **services**     | •  | •  | •  | •  | •  | •  | •  | •  |    |    |
| **api**          | •  |    | •  | •  | •  | •  |    | •  | •  |    |
| **main.py**      | •  |    |    | •  |    |    |    | •  |    | •  |

Everything is below the diagonal. That is the DAG, visible as a shape.

### Verify it yourself

The matrix is not hand-maintained trivia — you can regenerate it:

```bash
cd backend/app
for d in api core db geospatial models optimization schemas services simulation websocket; do
  echo "--- $d ---"
  grep -rho "from app\.[a-z_]*" $d --include=*.py | sed 's/from app\.//' | sort -u | tr '\n' ' '
  echo
done
```

If a new import ever makes a row reach to the right of the diagonal, the layering
has been broken and something needs rethinking.

---

## What each level means

**Level 0 — `core/` and `geospatial/`: the foundation.**
Neither imports anything from `app`. `geospatial/` is pure maths and SQL-expression
building; `core/` is settings, logging, errors and the Redis client. Because they
sit at the bottom, *any* package may use them freely — and no import of them can
ever create a cycle. That is why config and logging are safe to reach for anywhere.

**Level 1 — `db/` and `websocket/`: single-purpose infrastructure.**
`db/` owns the engine and session factory. `websocket/` owns the set of live
connections. Each needs only `core/`.

**Level 2 — `models/`: the shape of the database.**
The ORM classes. Needs `db/` for the declarative `Base`, and nothing else.

**Level 3 — `schemas/` and `optimization/`: two independent branches.**
They never import each other. Both touch `models/` for *only one thing*: the
shared enums (`OrderStatus`, `OptimizationObjective`, …). Keeping the enums in
`models/` and importing just those is what lets the solver stay testable without
a database — see [`optimization/README.md`](optimization/README.md).

**Level 4 — `simulation/`: the clock.**
Reads and writes real rows (`db/` + `models/`), measures distance
(`geospatial/`), and announces what it did (`websocket/`).

**Level 5 — `services/`: business logic, the widest importer.**
Imports from eight packages, and that is expected: a service's whole job is to
combine the lower layers into a rule. `optimization_service.py`, for instance,
loads rows with `models/`, solves with `optimization/`, and broadcasts with
`websocket/`.

**Level 6 — `api/`: the edge.**
Translates HTTP into service calls.

---

## The one honest exception

`api/` reaches directly into `simulation/` and `websocket/`, skipping `services/`.
Two files do it, and both are legitimate:

- **`api/routes/health.py`** reads `sim_engine.running` and `manager.count`.
  A health check reports on live objects; routing that through a service would
  add a file that does nothing but forward an attribute.
- **`api/routes/ws.py`** calls `manager.connect(ws)` directly, because handing
  over a WebSocket *is* the endpoint's entire job — there is no business rule.

Everything else in `api/` goes through `services/`. If you find yourself adding a
third exception, it probably belongs in a service.

---

## Package index

| Package | Job | README |
|---|---|---|
| `api/` | HTTP + WebSocket endpoints, auth dependencies | [→](api/README.md) |
| `core/` | Config, logging, errors, security, Redis, metrics | [→](core/README.md) |
| `db/` | Async engine, session factory, declarative base | [→](db/README.md) |
| `geospatial/` | Haversine distance, PostGIS query helpers | [→](geospatial/README.md) |
| `models/` | SQLAlchemy ORM classes, one per table | [→](models/README.md) |
| `optimization/` | VRP solver, greedy baseline, distance matrix | [→](optimization/README.md) |
| `schemas/` | Pydantic request/response contracts | [→](schemas/README.md) |
| `services/` | Business logic | [→](services/README.md) |
| `simulation/` | Vehicle-movement clock | [→](simulation/README.md) |
| `websocket/` | Connection registry + broadcast | [→](websocket/README.md) |

`main.py` sits beside them and wires the whole thing together.
