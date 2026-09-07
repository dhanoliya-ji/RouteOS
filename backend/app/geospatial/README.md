# `app/geospatial/` — distance and location queries

Two files, two completely different ways of answering a distance question:

```
   distance.py                        queries.py
   ───────────                        ──────────
   maths, in Python                   SQL, run by Postgres

   "how far apart are these           "which orders are within
    two points I already have?"        5 km of here?"

   used by the solver, the            used by the /nearby
   baseline, the simulation           endpoints
```

The split is *where the work happens*. `distance.py` computes on numbers already
in memory; `queries.py` builds SQL expressions so PostGIS can search the whole
table using an index — because "find everything near X" is a search problem, and
you never want to pull a million rows into Python to filter them.

This package imports **nothing** from `app/`. It is the only one that doesn't.

---

## File index

| File | Exports | Depends on |
|---|---|---|
| `distance.py` | `haversine_km`, `road_distance_km`, `travel_time_minutes` | `math` only |
| `queries.py` | `make_point`, `distance_meters`, `within` | `sqlalchemy.func` only |

---

## `distance.py` — great-circle distance

The Earth is round, so you cannot use Pythagoras on latitude and longitude. The
**haversine formula** gives the distance along the surface of a sphere:

```
   a = sin²(Δlat/2) + cos(lat₁)·cos(lat₂)·sin²(Δlon/2)
   d = 2R · asin(√a)                     R = 6371.0088 km
```

Two practical notes:

- **Everything is converted to radians first.** `math.sin` and friends take
  radians; feeding them degrees is the classic silent bug — no error, just
  answers that are wrong by a large factor.
- `R = 6371.0088` km is the Earth's mean radius. The planet is not a sphere
  (it bulges at the equator), so haversine carries a small inherent error —
  well under 1% at city scale, which is far smaller than the road-vs-straight-line
  error below.

### From straight line to road distance

A crow-flies distance is not a driving distance. Roads bend, one-way systems
force detours, rivers need bridges:

```
   road_distance_km(a, b) = haversine_km(a, b) × ROAD_DISTANCE_FACTOR
                                                        └─ 1.25 default
```

```
        B
        ▲                    haversine: 4.0 km  (the dashed line)
       ╱┊                    actual road: ~5.0 km
      ╱ ┊                    factor 1.25 bridges the gap
     ╱  ┊
    ╱   ┊
   A ─ ─ ┘
```

1.25 is a standard rule of thumb for urban road networks. It is an
*approximation* — and the honest reason it is acceptable here is that the
alternative is available: set `USE_OSRM=true` and `matrix.py` fetches real road
distances instead. The factor is the offline fallback, not the ambition.

### From distance to time

```
   travel_time_minutes = distance_km / AVERAGE_SPEED_KMH × 60
```

One flat average speed (30 km/h default). Real travel time varies with traffic,
time of day and road class — none of which is modelled. The guard clause returns
`0.0` for a non-positive speed rather than dividing by zero.

---

## `queries.py` — PostGIS, and the lon/lat trap

These functions do not compute anything. They build **SQL expressions** that
SQLAlchemy drops into a query, so the work happens inside Postgres:

```python
dist = distance_meters(Order.location, lat, lon)
stmt = (select(Order, dist)
        .where(within(Order.location, lat, lon, radius_km))
        .order_by(dist)
        .limit(50))
```

That becomes a single SQL statement. Postgres uses the GiST index to discard
almost every row without examining it, sorts the survivors by true distance, and
returns 50. Nothing else crosses the wire.

### The trap: `ST_MakePoint` takes longitude first

```python
func.ST_MakePoint(longitude, latitude)      # <-- lon, lat. NOT lat, lon.
```

Humans say "latitude, longitude". PostGIS follows the `(x, y)` convention, and
`x` is longitude. Get it backwards and there is no error — you silently place
Delhi (28.5 N, 77.2 E) at 77.5 N, 28.2 E, in the Arctic Ocean. Every distance is
then wrong and nothing complains.

This is exactly why `make_point(latitude, longitude)` exists as a wrapper: the
helper takes the human order and does the swap once, in one place, so no caller
has to remember.

### `SRID 4326` and `Geography` vs `Geometry`

```python
Geography(geometry_type="POINT", srid=4326)
```

- **4326** is WGS 84 — plain GPS latitude/longitude degrees.
- **`Geography`**, not `Geometry`, is the load-bearing choice:

| | `Geometry` | `Geography` |
|---|---|---|
| treats coordinates as | points on a flat plane | points on a globe |
| `ST_Distance` returns | degrees (meaningless as a length) | **metres** |
| distance across a city | wrong | correct |

With `Geometry`, "distance" would come back in degrees — and a degree of
longitude is a different number of kilometres at every latitude, so the value
could not be compared or thresholded. `Geography` does the spherical maths and
answers in metres. That is why every helper here can talk in metres and simply
multiply km by 1000.

### `within` vs `distance_meters` — index vs sort

The two are used together and do different jobs:

```
   within(...)          -> ST_DWithin(location, point, radius_m)
                           a PREDICATE. GiST-indexable, so Postgres
                           skips most rows without measuring them.

   distance_meters(...) -> ST_Distance(location, point)
                           a VALUE. Computed per surviving row, used
                           to sort and to report the actual distance.
```

Order matters for performance: filter with the indexable predicate *first*, then
measure only what survived. Sorting by `ST_Distance` alone would compute a
distance for every row in the table.

---

## Where each is used

| Caller | Uses | For |
|---|---|---|
| `optimization/matrix.py` | `road_distance_km`, `travel_time_minutes` | the haversine fallback matrix |
| `optimization/baseline.py` | `road_distance_km` | greedy "which is nearest?" |
| `simulation/engine.py` | `haversine_km` | raw segment length — no road factor, because it is interpolating along a straight line it already drew |
| `services/order_service.py` | `make_point`, `within`, `distance_meters` | `/orders/nearby` |
| `services/depot_service.py` | `make_point` | keeping `location` in step with lat/lng |
| `services/fleet_service.py` | *builds its own* | see below |

### Why `fleet_service` builds its own point

`depots` and `orders` have a stored `location` geography column. **`vehicles` do
not** — they keep plain `current_latitude` / `current_longitude` floats, because
a moving vehicle's position is rewritten every second and maintaining a geography
column (plus its index) on every tick would cost more than it returns.

So `vehicles_nearby` casts the two floats into a geography point *inside the
query*:

```python
veh_point = func.cast(
    func.ST_SetSRID(func.ST_MakePoint(Vehicle.current_longitude,
                                      Vehicle.current_latitude), 4326),
    Geography(),
)
```

The trade-off is explicit: this cannot use a spatial index, so it is a full scan.
That is fine for a fleet of tens or hundreds and would not be for millions —
at which point vehicles would get a real geography column and pay the write cost.
