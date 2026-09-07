# `src/pages/` — one file per screen

Eleven screens. Each is a default-exported component, mounted by `App.tsx`.

Nine are variations on one pattern. Two — `RoutePlanner` and `LiveOps` — are
where the interesting behaviour lives, and are worth reading in full.

---

## The pattern the CRUD screens share

`Orders.tsx` is the fullest example; `Fleet`, `Depots` and `ActiveRoutes` are
the same shape with fewer parts.

```jsx
export default function Orders() {
  const qc = useQueryClient();
  const push = useToast((s) => s.push);          // 1. how to tell the user
  const editable = canManage(useAuth(s => s.user?.role));  // 2. may they write?

  const [page, setPage] = useState(1);           // 3. local UI state only
  const [status, setStatus] = useState("");

  const orders = useQuery({                      // 4. server state
    queryKey: ["orders", page, status, search],  //    filters IN the key
    queryFn: () => orderApi.list({ page, status, search }),
  });

  const cancelMut = useMutation({                // 5. a write
    mutationFn: (id) => orderApi.cancel(id),
    onSuccess: () => { push("…"); qc.invalidateQueries({ queryKey: ["orders"] }); },
    onError: (e) => push(e.message, "error"),
  });

  return (
    <>
      <PageHeader title="Orders" actions={editable && <button/>} />
      {q.isLoading ? <Skeleton/> : empty ? <EmptyState/> : <table/>}
      <Modal>{/* the create/edit form */}</Modal>
    </>
  );
}
```

### The two habits worth copying

**Filters belong in the query key.**

```js
queryKey: ["orders", page, status, priority, search]
```

The key *is* the cache identity, so changing a filter is automatically a
different cached entry and refetches — no `useEffect` watching the filters. It
also means going back to a previous filter is instant, because that entry is
still cached.

**Writes invalidate; they never patch.**

```js
onSuccess: () => qc.invalidateQueries({ queryKey: ["orders"] })
```

Nothing edits the cached array by hand. The key is invalidated and the list is
refetched, so what the screen shows is what the server actually stored — a
hand-patched cache is how a UI ends up disagreeing with the database.

Note `["orders"]` invalidates *every* page and filter combination, because
TanStack Query matches by key prefix. That is intended: a cancelled order may
belong to any of them.

**Filters reset the page.** Every filter's `onChange` also calls `setPage(1)`.
Without it, filtering while on page 4 would request page 4 of a shorter result
set and show an empty table.

---

## Screen index

| File | Lines | Notes |
|---|---|---|
| `Login.tsx` | 84 | The only screen outside `Layout`. Lists demo accounts as one-click fills. |
| `Dashboard.tsx` | 136 | KPI tiles + Recharts charts + activity feed. |
| `Orders.tsx` | 241 | The reference CRUD screen: paging, three filters, search, modal form. |
| `Fleet.tsx` | 131 | Vehicle CRUD. Contains its own form sub-component. |
| `Depots.tsx` | 74 | Depot CRUD, with a small map. |
| `RoutePlanner.tsx` | 290 | **The core screen** — see below. |
| `ActiveRoutes.tsx` | 74 | Routes and their stops, read-only. |
| `LiveOps.tsx` | 221 | **The live screen** — see below. |
| `Analytics.tsx` | 77 | Charts over the reporting endpoints. |
| `OptimizationHistory.tsx` | 54 | The solver run log. |
| `Settings.tsx` | 39 | Current user's profile. Read-only. |

---

## `RoutePlanner.tsx` — the three-panel workflow

```
   ┌────────────┬──────────────────────────┬─────────────────┐
   │ PENDING    │                          │ vehicles        │
   │ ORDERS     │         MAP              │ objective       │
   │            │                          │ [ Optimize ]    │
   │ ☑ ORD-0041 │  depot ◆                 │ ─────────────── │
   │ ☑ ORD-0042 │  stops ● coloured by     │ progress bar    │
   │ ☐ ORD-0043 │        route             │  or             │
   │            │  polylines per vehicle   │ the plan +       │
   │ 2 selected │                          │ baseline compare │
   └────────────┴──────────────────────────┴─────────────────┘
        col-3              col-6                 col-3
```

The screen mirrors the backend's solve → review → decide workflow: the plan is
displayed and **nothing is dispatched** until "Accept Plan" is pressed.

### It polls a background job

The solve is queued, not awaited, so the mutation drives its own polling loop:

```js
const queued = await optimizationApi.startJob({...});   // returns immediately
for (;;) {
  await sleep(2000);
  const latest = await optimizationApi.get(queued.id);
  if (latest.status === "COMPLETED") return latest;
  if (latest.status === "FAILED") throw new Error(latest.error_message);
}
```

Why not just await the solve: a good solve wants minutes, which is longer than
an HTTP request should be held open. Queueing it lets the backend spend a
minutes-long budget while the UI stays responsive.

**Progress arrives by a different route than the result.** Polling detects
*completion*; the percentage comes from `OPTIMIZATION_PROGRESS` events over the
WebSocket, filtered to this run:

```js
if (e.type === "OPTIMIZATION_PROGRESS" && e.data?.run_id === jobId) …
```

That `jobId` comparison is why the socket handler must see current state, and
therefore why `useFleetSocket` uses a latest-ref (see `hooks/README.md`).

**Note the loop has no attempt cap.** A run stuck in `PROCESSING` would be
polled every two seconds indefinitely. A maximum attempt count, or a deadline,
is the obvious hardening.

### It surfaces when the solver lost

When `plan_source === "baseline"` the screen renders an explicit amber warning
rather than quietly showing a ~0% improvement — including the solver's own best
figures and the suggestion to raise the time budget. That honesty is deliberate:
the alternative reads as "optimization achieved nothing" when the truth is "the
solver ran out of time and we shipped the better plan".

---

## `LiveOps.tsx` — the live map

The only screen whose primary data source is the socket rather than a query.

```
   WebSocket event                  what it updates
   ─────────────────                ───────────────
   SNAPSHOT                    ──▶  replaces all vehicle positions (on connect)
   VEHICLE_LOCATION_UPDATED    ──▶  one vehicle's position
   DELIVERY_COMPLETED          ──▶  the event feed
   ROUTE_STATUS_UPDATED        ──▶  the feed + invalidates the routes query
   ROUTE_DELAYED               ──▶  the amber warning banner + feed
   ROUTE_REOPTIMIZED           ──▶  the feed, and clears the warning
   SIMULATION_STOPPED          ──▶  the feed
```

Positions are keyed by `vehicle_id` in a plain object and replaced per event, so
a moving fleet re-renders only what changed.

`SNAPSHOT` replacing *everything* is what makes a reconnect correct: the client
does not try to reconcile a gap, it just adopts the server's current truth.

Note the socket also invalidates a TanStack query on `ROUTE_STATUS_UPDATED` —
the two systems meeting deliberately, because a completed route changes the
route *list*, which is server state.

### The feed keeps a ref alongside state

```js
feedRef.current = [newMsg, ...feedRef.current].slice(0, 40);
setFeed([...feedRef.current]);
```

The ref avoids a stale closure: `pushFeed` is called from the socket handler,
which was created earlier, so reading `feed` directly could append to an
outdated array. The `slice(0, 40)` caps the feed so a long simulation cannot
grow it without bound.

A functional update — `setFeed(prev => [msg, ...prev].slice(0, 40))` — would
achieve the same thing with one source of truth instead of two, and is a
worthwhile simplification.

---

## A note on `Login.tsx`

It hardcodes the three demo passwords as one-click fills. Intentional for a demo
— they are the same credentials documented in the repo README and seeded by
`backend/scripts/seed_data.py`, so nothing is exposed that is not already
public. A real deployment overrides them via the `DEMO_*` settings, at which
point these buttons stop working and should be removed.

`Login` is also the one screen that manages its own loading and error state with
`useState` rather than a mutation — reasonable, since it drives the auth store
rather than a query.
