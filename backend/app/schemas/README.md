# `app/schemas/` — the API's public contract

Pydantic models describing **what may come in** and **what goes back out**.
They are not database tables — those are in [`../models/`](../models/).

---

## Why this folder exists at all

The obvious objection: `models/` already describes an order, so why write it
again? Because a stored row and a public payload are different things that only
*look* alike, and they change for different reasons.

```
        ┌──────────────────┐          ┌──────────────────┐
        │  schemas/Order*  │          │  models/Order    │
        │──────────────────│          │──────────────────│
        │ what a client    │          │ what Postgres    │
        │ may send, and    │   ≠      │ stores           │
        │ what it is       │          │                  │
        │ allowed to see   │          │                  │
        └──────────────────┘          └──────────────────┘

   the model has            the schema has            the schema
   things clients           things the table          rejects things
   must not set             does not store            the column
   ─────────────────        ────────────────          would accept
   order_number             distance_km (computed,    latitude = 200
   location (PostGIS)         on NearbyOrder)         weight_kg = -5
   status                                             window_end < start
```

Two concrete payoffs:

1. **Nothing leaks by accident.** `OrderOut` lists its fields explicitly, so
   adding an internal column to the model — a cost, a supplier note, a PostGIS
   blob — does *not* silently appear in the API response.
2. **Nothing is trusted by accident.** `order_number` is assigned by the server.
   It isn't on `OrderCreate`, so a client cannot set it even by trying.

The cost is real (some field names appear twice), and it buys the ability to
change storage without breaking clients, and to reject bad input before it ever
reaches a service.

---

## The Create / Update / Out triad

Most resources follow the same three-shape pattern. Once you see it, every file
here reads the same way:

```
   ┌──────────────┐   POST    fields a client may supply.
   │ XxxCreate    │           Required means required.
   └──────────────┘

   ┌──────────────┐   PATCH   every field optional, so
   │ XxxUpdate    │           "not mentioned" ≠ "set to null"
   └──────────────┘

   ┌──────────────┐   response  exactly what we choose to
   │ XxxOut       │             expose, and nothing else
   └──────────────┘             (model_config = from_attributes)
```

`from_attributes=True` on the `Out` models is what lets a handler return a
SQLAlchemy row directly — Pydantic reads it attribute by attribute instead of
requiring a dict.

### Why `Update` has every field optional

This is the difference between the two verbs, and it matters:

```
   PUT-style (not used here)      PATCH-style (used here)
   ─────────────────────────      ───────────────────────
   send the whole object          send only what changed
   omitted field -> cleared       omitted field -> untouched
```

The services rely on it:

```python
data.model_dump(exclude_unset=True)   # only keys the client actually sent
```

`exclude_unset` is what distinguishes *"don't touch the phone number"* from
*"clear the phone number"*. Without it, every PATCH would blank every field the
client didn't mention.

---

## File index

| File | Shapes | Notes |
|---|---|---|
| `common.py` | `Page[T]`, `Message`, `ErrorResponse` | Reusable across resources |
| `auth.py` | `UserRegister`, `UserLogin`, `Token`, `UserOut` | `UserOut` has no password field — by construction |
| `order.py` | `OrderBase/Create/Update/Out`, `NearbyOrder` | The fullest example of the pattern |
| `vehicle.py` | `VehicleCreate/Update/Out` | |
| `depot.py` | `DepotCreate/Update/Out` | |
| `route.py` | `RouteOut`, `RouteStopOut` | Read-only: routes come from the solver, not from clients |
| `optimization.py` | `OptimizationRequest`, `PlannedRoute`, `BaselineComparison`, … | Describes the solver's output |

Note what's missing: **`route.py` has no `Create`.** You cannot POST a route.
Routes exist only by accepting an optimization plan, and the absence of a schema
is what makes that unforgeable through the API.

---

## `Page[T]` — one generic instead of six

```python
class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int
```

Written once, reused as `Page[OrderOut]`, `Page[VehicleOut]`, and so on. The
generic parameter keeps it fully typed — OpenAPI generates a distinct schema per
instantiation, so `/docs` shows the real item shape rather than a vague list.

`total` and `pages` are both returned on purpose: `total` is the row count,
`pages` is `ceil(total / page_size)`. The client needs `pages` to render a pager
and would otherwise have to recompute it and risk disagreeing.

---

## Validation happens here, not in services

Constraints live on the fields, so bad input is rejected before any business code
runs — and the client gets a 422 naming the offending field:

```python
latitude:  float = Field(ge=-90,  le=90)
longitude: float = Field(ge=-180, le=180)
weight_kg: float = Field(gt=0)            # gt, not ge — a 0 kg delivery is not a thing
password:  str   = Field(min_length=8, max_length=128)
```

Cross-field rules need a validator, because no single field can see another:

```python
@model_validator(mode="after")
def _check_window(self):
    if start and end and end <= start:
        raise ValueError("delivery_window_end must be after delivery_window_start")
```

`mode="after"` runs once every field has been parsed, so both values are real
`datetime`s by then rather than raw strings.

The division of labour is worth stating plainly:

```
   schemas   →  "is this input well-formed?"        (a 400-class problem)
   services  →  "is this action allowed right now?" (a 409-class problem)
```

A negative weight is malformed — that's a schema's job. Editing a DELIVERED
order is well-formed but forbidden — that's a service's job, and it raises
`APIError`. Keeping the two apart is why neither file has to do the other's work.

---

## `EmailStr` needs a dependency

`auth.py` uses `EmailStr`, which is why `email-validator` sits in
`requirements.txt`. Pydantic doesn't validate emails without it — the import
fails loudly rather than silently accepting anything.
