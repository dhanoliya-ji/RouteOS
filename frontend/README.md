# `frontend/` — the RouteOS web client

A React single-page application. Ten screens over the backend API, plus a live
map driven by a WebSocket.

It holds **no business logic**. It does not decide which vehicle takes which
order, and it does not animate vehicles between waypoints — the backend decides
both, and this renders what it is told. That constraint is what keeps the map,
the order table and the analytics all agreeing with each other.

---

## Three kinds of state, three tools

The most useful thing to understand before reading any component. Picking the
wrong one is the usual cause of confusing UI bugs.

```
   ┌──────────────────────────────────────────────────────────────────┐
   │ SERVER STATE — data that lives in Postgres                       │
   │                                                                  │
   │   TanStack Query (useQuery / useMutation)                         │
   │   orders, vehicles, depots, routes, runs, analytics               │
   │                                                                  │
   │   Cached, deduped and refetched for you. Never copied into        │
   │   useState — that is how a screen ends up showing a stale row     │
   │   after a mutation.                                               │
   └──────────────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────────┐
   │ GLOBAL CLIENT STATE — ours, needed in unrelated places           │
   │                                                                  │
   │   Zustand (src/stores/)                                           │
   │   the signed-in user · the toast queue                            │
   │                                                                  │
   │   Two stores, both tiny. Anything only one screen cares about     │
   │   does NOT belong here.                                           │
   └──────────────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────────┐
   │ LOCAL STATE — this screen, this moment                            │
   │                                                                  │
   │   useState                                                        │
   │   which filter is chosen · which rows are ticked · modal open?    │
   └──────────────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────────┐
   │ LIVE STATE — pushed by the backend, not fetched                   │
   │                                                                  │
   │   useFleetSocket + useState                                       │
   │   vehicle positions · the event feed · solver progress            │
   │                                                                  │
   │   Deliberately NOT in Query's cache: these arrive as a stream of  │
   │   events rather than as a resource you can re-request.            │
   └──────────────────────────────────────────────────────────────────┘
```

---

## Structure

```
   src/
   ├── main.tsx          mounts React, installs the providers
   ├── App.tsx           the route table + the auth gate
   ├── index.css         Tailwind layers + the .btn/.card/.input classes
   │
   ├── api/              the ONLY place fetch() is called
   ├── types/            TypeScript mirrors of the backend's payloads
   ├── stores/           Zustand: auth + toasts
   ├── hooks/            useFleetSocket
   ├── utils/            Leaflet icon/colour helpers
   ├── components/       Layout (the shell) + ui (the primitives)
   └── pages/            one file per screen
```

Each folder has its own `README.md`.

---

## How a screen is put together

Every page follows the same shape, so once you have read one the rest are
familiar:

```
   PageHeader          title, subtitle, and the action buttons
        │
   useQuery(...)       fetch what the screen shows
        │
   isLoading ? <Skeleton/> : data.length ? <table/> : <EmptyState/>
        │
   useMutation(...)    a write, with onSuccess/onError
        │              ├─ push(...)                 tell the user
        │              └─ invalidateQueries(...)    refetch what changed
        │
   <Modal>             create/edit forms live in a modal, not a route
```

The `invalidateQueries` step is the one to remember. Nothing manually patches
the cached list after a write — the query key is invalidated and TanStack Query
refetches, so what you see is what the server actually stored.

---

## Talking to the backend

```
   pages/  ──▶  api/endpoints.ts  ──▶  api/client.ts  ──▶  fetch()
                (one object per      (auth header,
                 resource)           error envelope,
                                     401 handling)

   hooks/useFleetSocket.ts  ──▶  WebSocket  /ws/fleet
```

Two rules, both worth keeping:

- **Only `api/client.ts` calls `fetch`.** So the auth header, the error
  translation and the 401-redirect exist in exactly one place.
- **Only `api/endpoints.ts` knows URLs.** A page names a method
  (`orderApi.cancel(id)`), never a path.

---

## Screens

| Route | File | What it does |
|---|---|---|
| `/login` | `Login.tsx` | Sign in. Lists the demo accounts as one-click fills. |
| `/` | `Dashboard.tsx` | KPI tiles, charts, recent activity. |
| `/orders` | `Orders.tsx` | Paginated table with filters + search; create/edit/cancel. |
| `/fleet` | `Fleet.tsx` | Vehicle list; add/edit. |
| `/depots` | `Depots.tsx` | Depot list; add/edit. |
| `/planner` | `RoutePlanner.tsx` | **The core screen.** Pick orders + vehicles, solve, review, accept. |
| `/routes` | `ActiveRoutes.tsx` | Routes and their stops. |
| `/live` | `LiveOps.tsx` | Live map, simulation controls, traffic injection, re-optimize. |
| `/analytics` | `Analytics.tsx` | Charts over the reporting endpoints. |
| `/optimization` | `OptimizationHistory.tsx` | The solver run log. |
| `/settings` | `Settings.tsx` | The current user's profile. |

`RoutePlanner` and `LiveOps` are where the interesting behaviour is; the rest
are CRUD screens over the same pattern.

---

## Auth

```
   Login ──▶ POST /auth/login ──▶ token ──▶ localStorage
                                              │
                          every request adds  │  Authorization: Bearer …
                                              ▼
                            any 401 ──▶ clear token ──▶ /login
```

`App.tsx` wraps every real route in `<Protected>`, which waits for
`loadUser()` to resolve before deciding — without that wait, a page refresh
would bounce a signed-in user to the login screen while the token was still
being checked.

Role gating is advisory only: `canManage(role)` hides buttons a VIEWER cannot
use. **The backend enforces permissions**; hiding a button is a courtesy, not a
control.

---

## Running it

```bash
cd frontend
npm ci
npm run dev        # http://localhost:5173, proxying to localhost:8000

npm run lint       # tsc --noEmit — the only automated check there is
npm run build      # typecheck + production bundle
```

There are **no tests**. `npm run lint` (a typecheck) is the whole safety net,
which is worth knowing before changing anything non-obvious.

| Env var | For |
|---|---|
| `VITE_API_BASE_URL` | explicit backend origin |
| `VITE_WS_BASE_URL` | explicit WebSocket origin |
| `VITE_API_HOST` | bare backend hostname, injected by managed hosts |

All three are read at **build** time, not run time — a Vite bundle has the
values baked in, so changing one means rebuilding.

---

## Root files

| File | Purpose |
|---|---|
| `index.html` | The single page. Vite injects the bundle here. |
| `vite.config.ts` | Dev server + the `/api` proxy that avoids CORS locally. |
| `tailwind.config.js` | The `ink`/`brand` palettes the whole UI is built from. |
| `postcss.config.js` | Wires Tailwind and autoprefixer into the build. |
| `tsconfig.json` | Strict mode on. |
| `Dockerfile` | Two stages: build with Node, serve the static files with nginx. |
| `nginx.conf` | Serves the bundle and rewrites unknown paths to `/index.html`, which is what makes client-side routing survive a refresh. |

---

## Known limits

- **No error UI on reads.** Pages handle `isLoading` but never `isError`, so a
  failed request renders as "no data" rather than "something is wrong". An
  `ErrorState` component exists in `components/ui.tsx` for exactly this and is
  not yet wired up. Mutations *do* report failures, via toasts.
- **One 862 kB JS chunk** (≈249 kB gzipped) — Vite warns about it on every
  build. Leaflet, Recharts and React are all in the single entry bundle;
  route-level `lazy()` splitting is the usual fix.
- **No tests.**
- **The WebSocket is unauthenticated**, because a browser cannot send an
  `Authorization` header on a handshake. See the backend's
  `app/api/README.md`.
