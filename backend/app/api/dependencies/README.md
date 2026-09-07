# `app/api/dependencies/` — reusable pre-checks

FastAPI **dependencies**: functions that run *before* a handler and can reject
the request outright. One file, `auth.py`, answering two questions:

```
   who is calling?        -> get_current_user
   may they do this?      -> require_roles(...)
```

For the endpoint catalogue and which guard each route uses, see
[`../README.md`](../README.md).

---

## Why a dependency instead of a helper function

A dependency is *declared*, not called:

```python
async def create_order(..., _=Depends(_manage)):
    ...                       # body only runs if the guard passed
```

Three things follow, and together they are the reason this is not just a helper:

1. **It cannot be forgotten silently.** The permission is part of the
   signature, so reading a handler tells you who may call it.
2. **It short-circuits.** The guard raises before the body runs — there is no
   path where the handler executes unauthorised.
3. **It appears in OpenAPI.** `/docs` shows the endpoint as secured and offers
   the Authorize button, because the dependency chain includes `oauth2_scheme`.

The `_=` naming is conventional: the return value is unused, we want only the
side effect of the check. Where the user *is* needed, it's bound properly —
`user: User = Depends(get_current_user)`.

---

## `get_current_user` — token to user

```
   Authorization: Bearer eyJhbGc...
              │
              ▼
   oauth2_scheme extracts the token          missing ──> 401 NOT_AUTHENTICATED
              │
              ▼
   decode_access_token(token)                bad sig / expired
     verify signature (HS256, SECRET_KEY)    malformed sub
     check exp                          ────────> 401 INVALID_TOKEN
              │
              ▼
   SELECT user WHERE id = payload["sub"]
              │
              ▼
   user exists AND is_active?                no ──> 401 NOT_AUTHENTICATED
              │
              ▼
   return User
```

**Why it hits the database on every request.** The token already carries the
user id and role, so this lookup could be skipped. It isn't, because a JWT is
valid until it expires — and `access_token_expire_minutes` defaults to **1440
(24 hours)**. Without the lookup, deactivating an account would leave it working
for up to a day. The `is_active` check is the revocation mechanism, and it costs
one indexed primary-key read.

**Why the exception types are narrow.**

```python
except (jwt.PyJWTError, KeyError, ValueError) as exc:
```

Each maps to a real failure: `PyJWTError` for a bad or expired signature,
`KeyError` if `sub` is absent, `ValueError` if it won't parse as an int. A bare
`except` here would also swallow a genuine bug in the lookup and report it as
"invalid credentials", which is the kind of thing that costs an afternoon.

---

## `require_roles` — a dependency factory

The pattern is a **closure**: a function that builds and returns a dependency,
so the roles are captured once and the guard is reusable.

```python
def require_roles(*roles: UserRole):
    async def _guard(user: User = Depends(get_current_user)) -> User:
        if user.role == UserRole.ADMIN:
            return user                    # admin bypasses everything
        if user.role not in roles:
            raise _FORBIDDEN
        return user
    return _guard
```

Note that `_guard` itself depends on `get_current_user`. FastAPI resolves the
chain, so authentication happens automatically before authorization — you never
have to declare both.

**The ADMIN short-circuit is the role ladder in one line.** It means
`require_roles(DISPATCHER)` reads as *"dispatcher or admin"*, so no endpoint has
to spell out `require_roles(DISPATCHER, ADMIN)` and nobody can forget the second
argument.

Two convenience guards are pre-built at module level:

```python
require_dispatcher = require_roles(UserRole.DISPATCHER)
require_admin      = require_roles(UserRole.ADMIN)
```

---

## Two details that fix specific problems

**1. `auto_error=False` on the scheme.**

```python
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=..., auto_error=False)
```

By default, FastAPI's bearer scheme raises its own `HTTPException` when the
header is missing — before our code runs, producing a bare
`{"detail": "Not authenticated"}` that bypasses the error envelope in
`core/errors.py`. Turning it off makes the token `None` instead, so
`get_current_user` raises `APIError` and an auth failure looks like every other
error the API returns.

**2. The two error objects are module-level constants.**

```python
_UNAUTH    = APIError("NOT_AUTHENTICATED", ..., status_code=401)
_FORBIDDEN = APIError("FORBIDDEN", ..., status_code=403)
```

Built once and re-raised, rather than constructed per rejection. They carry no
per-request state, so there is nothing to vary — and it keeps the two messages
identical everywhere they are raised.

Note the distinction they preserve:

| | Means | Fix |
|---|---|---|
| **401** `NOT_AUTHENTICATED` | we don't know who you are | log in |
| **403** `FORBIDDEN` | we know, and you may not | ask an admin |

Collapsing these into one status is a common mistake; keeping them apart is what
lets a client tell "your session expired" from "you lack permission".

---

## What is deliberately *not* here

- **No password checking.** That's `core/security.py`, called by
  `routes/auth.py` at login. This folder only reads tokens.
- **No per-object permissions.** Roles are global — a DISPATCHER may edit *any*
  order, not just their depot's. Adding "own depot only" would mean a dependency
  that loads the object and compares, which is a different shape from these.
- **No WebSocket guard.** `/ws/fleet` is unauthenticated; see the note in
  [`../README.md`](../README.md) for why a browser cannot send a header on the
  handshake.

---

## Adding a new guard

Follow the existing shape — build a closure that depends on `get_current_user`:

```python
def require_active_depot(depot_id_param: str = "depot_id"):
    async def _guard(user: User = Depends(get_current_user), ...):
        ...
        return user
    return _guard
```

Raise `APIError` rather than `HTTPException`, so the response stays in the shared
envelope.
