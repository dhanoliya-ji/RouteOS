# `src/stores/` — global client state

Two Zustand stores, both small. This folder is for state that is **ours** (not
the server's) and is needed in places too far apart to pass props between.

```
   auth.ts    who is signed in
   toast.ts   the queue of notification messages
```

That is the entire list, and keeping it that short is the point.

---

## What does NOT belong here

The most useful rule in this folder:

```
   Does the server own it?              ──▶ TanStack Query, not a store.
   (orders, vehicles, routes, runs)         Putting server data in a store
                                            means owning cache invalidation
                                            by hand, which is what Query
                                            already does properly.

   Does only one screen care?           ──▶ useState in that screen.
   (a filter, a checkbox, a modal flag)

   Is it ours AND needed far apart?     ──▶ here.
```

Both current stores pass that last test: the signed-in user is read by the
router guard, the sidebar and half the pages; a toast is *raised* deep inside a
mutation and *rendered* by a component at the app root.

---

## `auth.ts`

```
   login(email, password)
        │  POST /auth/login  ──▶ token ──▶ localStorage
        │  GET  /auth/me     ──▶ user
        ▼
   { user }

   loadUser()          on startup: is the stored token still good?
   logout()            drop the token and the user
```

### `loading` exists to prevent a flash

The field that looks redundant. On a page refresh the token is in
`localStorage` but the user object is not — it takes a round trip to `/auth/me`
to find out whether that token is still valid. `loading: true` is the initial
state, and `App.tsx`'s `<Protected>` waits on it:

```
   without `loading`            with `loading`
   ─────────────────            ─────────────
   user === null                loading === true
        │                            │
   redirect to /login           show "Loading…"
        │                            │
   (token was fine — the        user resolves ──▶ render the page
    user is bounced for                     └──▶ or redirect if truly signed out
    no reason)
```

Note it starts `true`, so nothing renders as "signed out" until a real answer
arrives.

### `loadUser` treats any failure as signed out

```js
catch { tokenStore.clear(); set({ user: null, loading: false }); }
```

A rejected `/auth/me` is treated as "this token is no good" and the token is
discarded. That is right for the common cases (expired, revoked, account
disabled) and slightly wrong for one: a transient network failure also logs the
user out. Worth knowing; distinguishing them would mean inspecting the error's
status.

### `canManage` is a courtesy, not a control

```js
export const canManage = (role) => role === "ADMIN" || role === "DISPATCHER";
```

Used to hide buttons a VIEWER cannot use. **The backend enforces permissions** —
this only avoids showing a control that would return 403. Never treat it as
security; a user can call the API directly.

It mirrors the backend's role ladder (`require_roles` short-circuits on ADMIN),
so the two agree by construction rather than by coincidence.

---

## `toast.ts`

An ephemeral message queue. The pattern every page uses:

```js
const push = useToast((s) => s.push);
...
onError: (e) => push(e.message, "error")
```

Rendered by `<ToastHost/>`, mounted once in `App.tsx` outside the router — so a
toast survives navigation instead of unmounting with the page that raised it.

### Ids come from a module counter

```js
let counter = 0;
const id = ++counter;
```

Not `Date.now()`, which can collide when two toasts are pushed in the same
millisecond, and not `Math.random()`. A monotonic counter is collision-free
within a page load, which is all a React `key` needs.

### Each toast dismisses itself

```js
setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 4000);
```

Two details:

- The updater is a **function** of previous state, not a captured snapshot.
  With `set({ toasts: [...] })` computed at push time, two overlapping toasts
  would each remove the other's entry.
- The timer is **not cancelled** on unmount. Harmless here — the store outlives
  every component and the callback only filters an array — but it does mean a
  toast pushed immediately before the app tears down leaves a pending timer.

`dismiss(id)` exists for manual removal. Note the toasts are not currently
clickable, so nothing calls it from the UI.
