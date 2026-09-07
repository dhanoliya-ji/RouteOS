# `src/` — the application source

Three files at this level plus seven folders. The files are the bootstrap; the
folders are the app.

| File | Job |
|---|---|
| `main.tsx` | Mount React and install the three providers |
| `App.tsx` | The route table and the auth gate |
| `index.css` | Tailwind layers plus the shared `.card` / `.btn` / `.input` classes |
| `vite-env.d.ts` | Types for `import.meta.env` and for importing `.png` files |

For what each folder does, see its own README. This file is about how they fit
together.

---

## Dependency direction

Imports point **downward**. Nothing in a lower row imports from a higher one.

```
   main.tsx                    installs providers, mounts the tree
       │
   App.tsx                     routes + the auth gate
       │
   pages/                      one screen each
       │
       ├──────────────┬──────────────┬─────────────┐
       ▼              ▼              ▼             ▼
   components/     hooks/        stores/       api/endpoints.ts
   (presentational) (socket)     (zustand)          │
       │                            │               ▼
       │                            └──────▶ api/client.ts
       │                                            │
       ▼                                            ▼
   utils/map.ts                                 types/
   (leaflet helpers)                        (compile-time only)
```

Two things worth noting:

- **`components/` never imports from `pages/`.** A component that needs
  page-specific knowledge is a sign it belongs in that page's file — which is
  why `RoutePlanner` keeps its own `Metric` and `Settings` its own `Row`.
- **`stores/auth.ts` imports `api/`, not the reverse.** The store drives the
  login call; the API layer knows nothing about who is signed in beyond reading
  the token from `tokenStore`.

`types/` is imported by nearly everything and imports nothing. It disappears at
build time.

---

## `main.tsx` — three providers, in this order

```jsx
<React.StrictMode>          // dev-only: double-invokes effects to surface bugs
  <QueryClientProvider>     // must wrap anything calling useQuery
    <BrowserRouter>         // must wrap anything using routes or <Link>
      <App />
```

The nesting order matters only in that both providers must be *outside* `App`.
`StrictMode` is worth knowing about while debugging: in development it mounts,
unmounts and remounts every component, so an effect runs twice. That is what
makes `useFleetSocket`'s cleanup (its `closed` flag) load-bearing rather than
theoretical — see `hooks/README.md`.

### The Query defaults are a deliberate policy

```js
{ retry: 1, refetchOnWindowFocus: false, staleTime: 10_000 }
```

| Setting | Instead of | Why |
|---|---|---|
| `retry: 1` | 3 | A failed request retries once. Three attempts against a down backend just delays the error the user needs to see. |
| `refetchOnWindowFocus: false` | `true` | The default refetches every query whenever the tab regains focus. On a screen with several queries that is a burst of requests for alt-tabbing. |
| `staleTime: 10_000` | `0` | Data is considered fresh for 10s, so remounting a screen within that window uses the cache instead of refetching. |

`staleTime` is the one to be aware of when something looks stale: a change made
elsewhere can take up to ten seconds to appear unless the write path
invalidated its key. Which is exactly why mutations call
`invalidateQueries` — that bypasses `staleTime` entirely.

Screens needing genuinely live data opt out per query with `refetchInterval`
(`LiveOps` polls active routes and simulation status every 5s).

---

## `App.tsx` — routing and the gate

```
   /login                    Login          (outside the shell)

   /                         <Protected><Layout/></Protected>
     ├─ index                Dashboard
     ├─ orders               Orders
     ├─ fleet · depots       Fleet · Depots
     ├─ planner · routes     RoutePlanner · ActiveRoutes
     ├─ live · analytics     LiveOps · Analytics
     └─ optimization · settings

   *                         redirect to /
```

**Nested routes with one guard.** Every real screen is a child of the `/` route,
so `<Protected>` wraps them all once — a new screen is protected by default
rather than by remembering to wrap it. `Layout` renders `<Outlet/>` where the
child goes.

**The catch-all redirects rather than 404s.** Any unknown path goes to `/`,
which then redirects to `/login` if not signed in.

### `<Protected>` has three states, not two

```jsx
if (loading) return <div>Loading…</div>;      // don't decide yet
if (!user)   return <Navigate to="/login" />; // decided: signed out
return children;                              // decided: signed in
```

The `loading` branch is the important one. On a page refresh the token is in
`localStorage` but the user object is not — confirming it takes a round trip.
Without this branch a signed-in user would be bounced to `/login` every refresh.

### `loadUser()` runs once, in an effect

```jsx
const loadUser = useAuth((s) => s.loadUser);
useEffect(() => { loadUser(); }, [loadUser]);
```

Selecting just the function (rather than destructuring the whole store) is what
keeps this from re-running: a Zustand action is a stable reference, so the
dependency array never changes. Destructuring the store would give a new object
each render.

`<ToastHost/>` sits outside `<Routes>` on purpose — a toast raised by an action
that navigates away must survive that navigation.

---

## `index.css` — the design system in seven classes

Tailwind's three layers, then component classes built with `@apply`:

```
   .card      white panel, rounded, bordered
   .btn       shared button base (padding, focus, disabled styling)
   .btn-primary / .btn-ghost / .btn-danger
   .input     text input / select
   .label     small form label
   .badge     pill, used by StatusBadge
```

**Why these exist at all**, when the point of Tailwind is utility classes: each
is a long utility string repeated dozens of times. `.input` appears 37 times and
`.label` 58 — inlining them would mean 58 copies of the same nine utilities, and
a restyle would be a find-and-replace. Extracting only the genuinely repeated
combinations keeps the benefit of utilities everywhere else.

Note `.btn-primary` is `@apply btn bg-brand-600 …` — composing the base class,
so disabled and focus behaviour is defined once.

The `.leaflet-container` rule is not cosmetic: Leaflet needs its container to
have a real height, and `height: 100%` plus the fixed-height shell in
`Layout.tsx` is what gives the map screens their size. A grey background shows
while tiles load.

### The palette

`tailwind.config.js` defines two scales, and the whole UI is built from them:

```
   brand-*   blue     the accent: primary buttons, active nav, links
   ink-*     slate    everything structural: text, borders, backgrounds
```

Sticking to two named scales is what makes the app look coherent. `ink-900` is
body text, `ink-400` is muted, `ink-200` is a border, `ink-50` is the page
background.

`Inter` is loaded from Google Fonts in `index.html`, with
`system-ui, sans-serif` as the fallback in the config — so the app renders
sensibly if that request is blocked.
