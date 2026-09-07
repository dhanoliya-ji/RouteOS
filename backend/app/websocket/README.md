# `app/websocket/` — live event fan-out

One file, one class: a registry of open browser connections and a `broadcast()`
that sends a message to all of them.

This is the *transport*. It generates no events of its own — the simulation
engine and the optimization service do that, and they call in here to publish.

---

## Why a WebSocket rather than polling

A dispatcher watching a map needs a position update roughly every second.

```
   POLLING                            WEBSOCKET
   ───────                            ─────────
   GET /vehicles every 1s             one connection, held open
   per client, forever                server pushes only when
                                      something changes

   20 clients = 20 req/s              20 clients = 20 idle sockets
   each: auth, query, serialise       each event serialised once
   always 1s stale                    arrives when it happens
```

The pattern here is **publish/subscribe**: publishers (the engine, the
optimizer) don't know who is listening, and subscribers don't ask for anything.

---

## `ConnectionManager`

```python
class ConnectionManager:
    _connections: set[WebSocket]      # who is listening
    _lock: asyncio.Lock               # guards mutations of that set
```

A `set` because membership is the only question asked — add, discard, iterate —
and all three are O(1) or O(n) with no ordering needed.

| Method | Does |
|---|---|
| `connect(ws)` | accept the handshake, add to the set |
| `disconnect(ws)` | discard from the set |
| `broadcast(type, data)` | send `{"type": …, "data": …}` to everyone |
| `count` | how many are listening (used by `/health` and `/metrics`) |

### The envelope

Every message has the same two-key shape:

```json
{ "type": "VEHICLE_LOCATION_UPDATED",
  "data": { "vehicle_id": 7, "latitude": 28.54, "longitude": 77.27 } }
```

So a client is one `switch` on `type`. Adding an event never changes the
envelope, which means an old client ignores a new event instead of breaking on
it.

### Broadcast survives dead clients

The subtle part of the class:

```python
dead = []
for ws in list(self._connections):        # iterate a COPY
    try:
        await ws.send_json(message)
    except Exception:
        dead.append(ws)                   # collect, don't remove yet
if dead:
    async with self._lock:
        for ws in dead:
            self._connections.discard(ws)
```

Three deliberate choices:

1. **Iterate a copy.** A client can disconnect *during* the loop, mutating the
   set; iterating the live set would raise `RuntimeError: Set changed size
   during iteration`.
2. **Collect failures, remove afterwards.** Same reason — you cannot discard
   from a set you are walking.
3. **One dead client does not block the rest.** A browser closed without a
   proper close frame only shows up as a failed send. Catching per-recipient
   means the other nineteen still get their update; without it, one stale
   socket would abort the whole broadcast and freeze every map.

### `count` is read without the lock

`/health` and `/metrics` read `manager.count` directly. `len()` on a set is
atomic enough for a gauge — the number may be one stale by the time it is
serialised, which is fine for a metric and not worth contending the lock for.

---

## Event catalogue

13 event types. Every one is a *fact that already happened* — the database was
already written before the broadcast went out, so a client can trust it without
confirming.

### On connect

| Type | Payload |
|---|---|
| `SNAPSHOT` | `engine.status()` — `running`, `speed_multiplier`, `active_vehicles`, and every vehicle's current position |

Sent by `api/routes/ws.py` the moment a client connects, so a map joining
mid-simulation can draw immediately instead of waiting for the next tick to
learn that anything exists.

### From the simulation engine

| Type | When | Payload |
|---|---|---|
| `VEHICLE_LOCATION_UPDATED` | every tick, per moving vehicle | `vehicle_id`, `route_id`, `latitude`, `longitude`, `speed_factor` |
| `ORDER_STATUS_UPDATED` | a delivery lands | `order_id`, `order_number`, `status` |
| `DELIVERY_COMPLETED` | same moment, delivery-centric | `order_id`, `route_id`, `stop_sequence` |
| `ROUTE_STATUS_UPDATED` | vehicle back at depot | `route_id`, `route_code`, `status` |
| `SIMULATION_STARTED` | `start()` succeeds | the full status object |
| `SIMULATION_STOPPED` | stopped, or all work done | `{"running": false}` |

`ORDER_STATUS_UPDATED` and `DELIVERY_COMPLETED` fire together on purpose: one is
for the orders table, the other for the activity feed, and each carries what
that view needs without the client joining anything.

### From the optimization service

| Type | When | Payload |
|---|---|---|
| `OPTIMIZATION_STARTED` | a job is queued | `run_id`, `orders_count`, `vehicles_count`, `objective`, `budget_seconds` |
| `OPTIMIZATION_PROGRESS` | every 5 s while solving | `run_id`, `elapsed_seconds`, `budget_seconds`, `progress_pct` |
| `OPTIMIZATION_COMPLETED` | plan ready | `run_id`, `status`, `improvement_percentage`, distances before/after, assigned/unassigned counts, `execution_time_ms`, `plan_source` |
| `OPTIMIZATION_FAILED` | solve raised | `run_id`, `error` |

`budget_seconds` is sent up front with `STARTED` so a progress bar knows its
scale before the first `PROGRESS` arrives.

### From the simulation service

| Type | When | Payload |
|---|---|---|
| `ROUTE_DELAYED` | traffic applied | `route_id`, `severity`, `speed_factor`, `added_delay_minutes`, `late_orders`, `remaining_stops` |
| `ROUTE_REOPTIMIZED` | stops re-sequenced | `route_id`, `reoptimized`, `new_sequence`, `remaining_distance_km` |

`ROUTE_DELAYED` carrying `late_orders` is the useful bit: the server has already
worked out which deliveries will now miss their windows, so the UI can raise an
alert without recomputing anything.

---

## Who publishes what

```
   simulation/engine.py ─────┐
                             │
   services/                 │
     optimization_service ───┼──► websocket/manager.py ──► every browser
     simulation_service ─────┘         broadcast()
```

Note the direction: `websocket/` sits *below* its publishers in the layer order
and imports none of them. Publishers import the manager. That is what keeps this
package a dumb pipe with no knowledge of routing or solving — and what stops a
cycle forming, since the engine already imports the manager.

---

## Limits

- **In-process only.** The set of connections lives in this Python process, so
  `broadcast()` reaches only clients attached to *this* instance. A second
  replica would talk to its own clients and no others. Fan-out across instances
  means a real message bus — Redis pub/sub is already a dependency and is the
  obvious next step.
- **No rooms or topics.** Every client gets every event and filters client-side.
  Fine at this scale; a large deployment would want per-depot subscriptions so a
  dispatcher isn't sent the whole country's traffic.
- **No replay.** Events are fire-and-forget. A client that disconnects for ten
  seconds misses whatever happened, which is why `SNAPSHOT` on reconnect matters
  — it re-establishes the truth without needing a log.
- **No auth on the socket.** See the note in
  [`../api/README.md`](../api/README.md): a browser cannot set an
  `Authorization` header on a WebSocket handshake.
