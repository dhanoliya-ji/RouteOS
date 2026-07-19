# scripts/

The runnable data/benchmark scripts live in **[`../backend/scripts/`](../backend/scripts/)** so they
ship inside the backend Docker image and share the app's models/session. Run them from the
`backend/` directory (or inside the container):

```bash
# Seed demo data (idempotent): users, 1 depot, 20 vehicles, ~150 orders
python -m scripts.seed_data

# Generate additional demo orders for load testing
python -m scripts.generate_demo_orders --count 500 --depot 1

# Benchmark the OR-Tools optimizer vs the naive baseline
SOLVER_TIME_LIMIT_SECONDS=3 python -m scripts.benchmark --sizes 50 100 250 500

# Inside Docker:
docker compose exec backend python -m scripts.generate_demo_orders --count 1000
```
