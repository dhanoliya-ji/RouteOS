# `src/api/` — the only place that talks to the backend

Two files, one boundary. Nothing outside this folder calls `fetch`, and nothing
outside it knows a URL.

```
   pages/ and stores/
        │  orderApi.cancel(7)
        ▼
   endpoints.ts        one object per resource. Knows paths and shapes.
        │  apiRequest("/orders/7/cancel", { method: "POST" })
        ▼
   client.ts           auth header · query params · error envelope · 401
        │  fetch(...)
        ▼
   the backend
```

**Why the split.** `client.ts` owns everything that is true of *every* request —
the token, the error shape, the 401 redirect — so those exist once. `endpoints.ts`
owns what is specific to each resource. A page therefore names an action, never
a path or a method, and no page can forget to attach the token.

| File | Owns |
|---|---|
| `client.ts` | Backend origin resolution, `tokenStore`, `ApiError`, `apiRequest` |
| `endpoints.ts` | One typed object per resource: `orderApi`, `vehicleApi`, … |

---

## `client.ts`

### Resolving the backend origin

Three inputs, in priority order — explicit URLs, then a bare hostname, then
localhost defaults. The middle case is the one with a story:

```
   VITE_API_HOST = "routeos-backend-h5x6"      (no dots, no scheme)
                        │
                        │  Render's `fromService.property: host` resolves to
                        │  the peer's *service name*, which is internal DNS.
                        │  This bundle runs in a browser, so it needs a public
                        │  origin.
                        ▼
   https://routeos-backend-h5x6.onrender.com
```

A dot-less value is therefore treated as a Render service name and expanded;
anything containing a dot is assumed to be a real hostname and left alone.

**All of this happens at build time.** `import.meta.env` values are inlined by
Vite, so the origin is baked into the bundle — changing an env var means
rebuilding, not restarting.

### The token

```js
tokenStore  →  localStorage["routeos_token"]
```

`localStorage`, not a cookie, because the backend expects a bearer header
rather than a session. The trade-off is worth naming: a bearer token in
`localStorage` is readable by any script on the page, so it is vulnerable to XSS
in a way an `HttpOnly` cookie is not. It also means the token survives a tab
close, which is why `loadUser()` on startup has to validate it rather than
trust it.

### `apiRequest` in order

1. **Build the URL.** `path.startsWith("http")` lets a caller pass an absolute
   URL; otherwise `/api/v1` is prefixed.
2. **Add query params**, skipping `undefined`, `null` *and* `""` — so an unset
   filter is omitted from the query string rather than sent as an empty value
   the backend would try to parse.
3. **Attach the token** if there is one.
4. **Encode the body.** JSON normally; `form: true` switches to
   `application/x-www-form-urlencoded`, which exists for exactly one endpoint —
   `/auth/login` follows the OAuth2 password flow and requires a form.
5. **Handle 401 globally.** Clear the token and redirect to `/login` — but *not*
   for `/auth/` paths, or a failed login attempt would reload the page instead
   of showing "incorrect password".
6. **Unwrap the error envelope.** The backend returns
   `{"error": {code, message, details}}`; FastAPI's own validation errors use
   `{"detail": ...}`. Both are handled, so a caller always gets an `ApiError`
   with a usable `.message`.
7. **Return.** `204` yields `undefined` rather than attempting to parse an empty
   body as JSON.

### `ApiError`

Carries `code`, `status` and `details` alongside the message. `code` is the
stable token to branch on (`"ORDER_IMMUTABLE"`); `message` is prose for a human
and is what the toast displays.

---

## `endpoints.ts`

One exported object per resource, each method a thin typed wrapper:

```ts
export const orderApi = {
  list:   (params) => apiRequest<Paginated<Order>>("/orders", { params }),
  cancel: (id)     => apiRequest<Order>(`/orders/${id}/cancel`, { method: "POST" }),
  ...
};
```

The type argument is the whole point — `apiRequest<Paginated<Order>>` is what
makes `orders.data.items[0].order_number` checked at compile time. It is an
*assertion*, not a validation: nothing verifies at runtime that the backend
really sent that shape, so a backend change that this file does not follow
becomes a runtime `undefined` rather than a type error. Keeping `src/types/`
in step with the API is a manual discipline.

### Two things to know

**Some methods are unused by the UI.** `orderApi.get`, `orderApi.nearby`,
`vehicleApi.get`, `vehicleApi.nearby`, `routeApi.get`, `depotApi.update`,
`authApi.register` and `optimizationApi.run` have no caller. They are kept
deliberately: this file is a client for the backend API, and a complete mirror
is more useful than one pruned to today's screens. (Note the comment on
`optimizationApi.run` mentions scripts and tests — there are none in the
frontend; the endpoint is simply the synchronous alternative to `startJob`.)

**`simulationApi` and parts of `analyticsApi` return `any`.** Those endpoints
return loosely-shaped diagnostic payloads that have no interface in
`src/types/`, so their responses are unchecked. It is the weakest typing in the
codebase and the obvious place to tighten if those payloads settle.

---

## Adding an endpoint

1. Add the response interface to `src/types/index.ts`.
2. Add the method to the right object here, with its type argument.
3. Call it from a page through `useQuery` / `useMutation`.

Do not call `apiRequest` directly from a page — that puts a URL in a component
and skips the layer that exists to prevent exactly that.
