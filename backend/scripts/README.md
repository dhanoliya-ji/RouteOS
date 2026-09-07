# `backend/scripts/` — command-line tools

Standalone programs you run **by hand**, not part of the running API.
They import the same `app.*` code the server uses (same models, same session,
same solver), so whatever they do is exactly what the real system would do.

Because they live inside `backend/`, they also ship inside the backend Docker
image — so you can run them in a container against a live database.

---

## The four scripts and how they relate

```
                       demo_geo.py
                  (fake Delhi NCR geography:
                   zones, streets, names)
                            |
                            | supplies random coordinates,
                            | addresses and customer names
                            v
                  generate_demo_orders.py
                  (writes N Order rows to the DB)
                            ^
                            | calls, to fill the depot with work
                            |
                       seed_data.py
              (users + 1 depot + 20 vehicles + ~150 orders)


                       benchmark.py
        (no database at all - feeds synthetic orders straight
         into the solver and prints a comparison table)
```

Two of them touch the database (`seed_data`, `generate_demo_orders`), one is a
pure data source (`demo_geo`), and one is pure computation (`benchmark`).

---

## File index

| File | Touches DB? | What it does |
|---|---|---|
| `demo_geo.py` | no | Constant tables of fictional NCR zones, streets and names, plus `random_point()` / `random_address()` / `random_customer()` helpers. Every coordinate is jittered around a real district centroid so demo orders spread believably across the metro. |
| `generate_demo_orders.py` | writes `orders` | Builds `count` `Order` rows for one depot — random weight, priority (weighted 20/45/25/10 across LOW→URGENT), a 2–4 h delivery window on 60% of them — and bulk-inserts them. |
| `seed_data.py` | writes `users`, `depots`, `vehicles`, `orders` | One-shot demo bootstrap. **Idempotent**: it creates the three demo logins if missing, then bails out early if any depot already exists, so re-running never duplicates a fleet. |
| `benchmark.py` | no | Measures the optimizer. For each problem size it runs `solve_vrp` and `nearest_neighbour` over the same synthetic orders and prints solve time, both distances, and the percentage gain. |

---

## Running them

All commands run from the `backend/` directory, so `python -m scripts.x`
resolves both `scripts.*` and `app.*`.

```bash
# Demo bootstrap — users, 1 depot, 20 vehicles, ~150 orders. Safe to repeat.
python -m scripts.seed_data

# Add more orders on top (load testing)
python -m scripts.generate_demo_orders --count 500 --depot 1

# Measure solver vs baseline at several problem sizes (no DB needed)
SOLVER_TIME_LIMIT_SECONDS=3 python -m scripts.benchmark --sizes 50 100 250 500
```

Inside Docker, prefix with the service name:

```bash
docker compose exec backend python -m scripts.seed_data
docker compose exec backend python -m scripts.generate_demo_orders --count 1000
```

---

## Notes

- **Seeded randomness.** `seed_data` uses `random.Random(42)` and `benchmark`
  uses `random.Random(123)`. Fixed seeds mean two runs produce identical data,
  so a benchmark number is reproducible and comparable across code changes.
- **All customer data is synthetic.** Names, phone numbers and addresses are
  generated from the tables in `demo_geo.py`; none of it is real.
- `benchmark.py` deliberately skips the database. It calls the solver directly,
  so the timings measure the optimizer alone with no query or network noise.
