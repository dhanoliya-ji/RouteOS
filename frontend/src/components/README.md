# `src/components/` — the shell and the shared primitives

Two files, split by scope:

| File | Holds | Used by |
|---|---|---|
| `Layout.tsx` | `Layout` (the app shell), `PageHeader` | the router; every page |
| `ui.tsx` | `StatusBadge`, `KpiCard`, `EmptyState`, `ErrorState`, `Skeleton`, `Modal`, `ToastHost` | pages, as needed |

Anything used by only one screen stays in that screen's file — `RoutePlanner`
keeps its own `Metric`, `Settings` its own `Row`. This folder is for what is
genuinely shared, which is why it is small.

Every component here is **presentational**: props in, markup out. None fetches
data, and the only one holding state is `ToastHost`, which subscribes to the
toast store.

---

## `Layout.tsx`

### `Layout` — the shell every signed-in page renders inside

```
   ┌─────────────┬──────────────────────────────────────┐
   │  sidebar    │  <Outlet/>                           │
   │             │                                      │
   │  logo       │    ← the current page renders here    │
   │  NAV links  │                                      │
   │             │      overflow-y-auto: the PAGE        │
   │  ─────────  │      scrolls, the sidebar does not    │
   │  user/role  │                                      │
   │  Sign out   │                                      │
   └─────────────┴──────────────────────────────────────┘
        w-60              flex-1
```

Mounted once by `App.tsx` as the parent route, so the sidebar is **not
remounted** on navigation — which is why moving between screens does not flash
the shell.

`h-screen overflow-hidden` on the wrapper with `overflow-y-auto` on the main
pane is what keeps the sidebar fixed while the content scrolls. It is also what
lets the map screens fill exactly the viewport: `RoutePlanner` and `LiveOps`
rely on that bounded height, so a map has a real size to occupy.

The `NAV` array is the single source of the sidebar. Adding a screen means one
entry here plus one `<Route>` in `App.tsx`.

`end: true` on the Dashboard entry is necessary: its path is `/`, which is a
prefix of every other route, so without `end` the Dashboard link would appear
active on every page.

### `PageHeader` — the title bar

Used by all ten pages, which is what makes them look like one application.
Takes `title`, an optional `subtitle` and optional `actions` (rendered right).
`subtitle` is a `ReactNode`, not a string — `LiveOps` passes the live
connection indicator through it.

It lives in `Layout.tsx` rather than `ui.tsx` because it is part of the page
frame rather than a reusable widget.

---

## `ui.tsx`

### `StatusBadge` — one lookup table for every status

```js
const STATUS_COLORS: Record<string, string> = { PENDING: "…", DELIVERED: "…", … };
```

One flat map covering **four different enums** — order status, vehicle status,
route status and priority. That works because the values do not collide, and it
means a status is coloured identically everywhere it appears.

Two deliberate touches:

- An unknown value falls back to neutral grey rather than rendering unstyled, so
  a new backend enum member degrades instead of breaking.
- `value.replaceAll("_", " ")` renders `OUT_FOR_DELIVERY` as `OUT FOR DELIVERY`,
  so the wire format never reaches the user.

### `Skeleton` is the loading idiom

A pulsing grey block, sized by the caller:

```jsx
{query.isLoading ? <Skeleton className="h-64" /> : <table>…</table>}
```

Used in nineteen places, and it is the *only* loading pattern — there is no
spinner. Consistency is the point: every screen loads the same way, and a
skeleton sized like the content it replaces avoids the layout jumping when data
arrives.

### `EmptyState` vs `ErrorState`

```
   EmptyState   "there is nothing here"     — used in 13 places
   ErrorState   "the request failed"        — used in 0
```

That asymmetry is a real gap, not a style choice. No page checks a query's
`isError`, so a failed request falls through to the empty branch and reads as
"no orders" when it should read "could not load orders". `ErrorState` exists for
exactly this and is not wired up.

The fix is mechanical, per query:

```jsx
{q.isLoading ? <Skeleton/> : q.isError ? <ErrorState message={...}/> : …}
```

Mutations are unaffected — they report failures through toasts.

### `Modal` — forms without routes

Create/edit forms are modals rather than pages. Two details in the
implementation:

- **`if (!open) return null`** — the modal is unmounted when closed, not
  hidden. So its form state resets between openings, which is what stops a
  previously-edited order's values appearing when you next click "New Order".
- **The backdrop closes it; the panel does not.** `onClick={onClose}` sits on
  the backdrop, and the inner panel calls `e.stopPropagation()`. Without that
  stop, clicking anywhere in the form would dismiss it.

`z-[900]` for the modal and `z-[1000]` for `ToastHost` are chosen so a toast
raised by a form's own mutation appears **above** the modal rather than behind
it.

### `ToastHost`

Rendered once in `App.tsx`, deliberately outside `<Routes>` so a toast survives
the navigation that follows a successful action. Reads the toast store; each
entry removes itself on a timer (see `stores/README.md`).
