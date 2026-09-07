# `src/types/` — the shape of the backend's data

One file. TypeScript interfaces mirroring what the API returns, plus the string
unions mirroring the backend's enums.

Nothing here runs. It exists entirely at compile time — every interface is
erased by the time the bundle is built.

---

## What it is really for

These types are a **hand-maintained copy of the backend's contract**, and it is
worth being clear-eyed about what that does and does not buy.

```
   backend/app/schemas/order.py          frontend/src/types/index.ts
   ────────────────────────────          ───────────────────────────
   class OrderOut(BaseModel):    ◀────▶  export interface Order {
       order_number: str                   order_number: string;
       weight_kg: float                    weight_kg: number;
       priority: OrderPriority             priority: Priority;
       ...                                 ...
                                         }
        the real contract                 our belief about it
```

**What you get:** autocomplete, and a compile error when a *component* misuses a
field — a typo'd `order.ordr_number`, or treating `weight_kg` as a string.

**What you do not get:** any guarantee the backend agrees. Nothing validates a
response at runtime, so if the API renames a field, TypeScript stays happy and
the value silently becomes `undefined` at the point of use. Keeping this file in
step with `backend/app/schemas/` is a manual discipline, and the most likely
source of a "works in dev, wrong in prod" bug in this codebase.

Runtime validation (zod, or generating these from the backend's OpenAPI schema
at `/openapi.json`) is the standard fix and is not done here.

---

## Contents

| Group | Types |
|---|---|
| Enums, as string unions | `Role`, `OrderStatus`, `Priority`, `VehicleStatus`, `RouteStatus`, `Objective` |
| Resources | `User`, `Depot`, `Order`, `Vehicle`, `Route`, `RouteStop` |
| Optimization | `Metrics`, `Comparison`, `OptimizationRun` |
| Envelopes | `Paginated<T>`, `DashboardSummary`, `WsEvent` |

### Enums are string unions, not TS `enum`s

```ts
export type Priority = "LOW" | "NORMAL" | "HIGH" | "URGENT";
```

Deliberate, and it matters. The backend serialises these as strings, so a union
of literals *is* the wire format — no conversion, and a value straight from
JSON is already the right type. A TypeScript `enum` would generate a runtime
object and invite `Priority.LOW` where the API expects `"LOW"`.

It also means the compiler catches a misspelled status in a comparison, which is
the main everyday benefit.

### `optional` mirrors `nullable`

```ts
vehicle_id: number | null;          // the column is nullable
customer_phone?: string | null;     // nullable AND may be absent
```

The `| null` is not decoration — it forces a check at the use site, which is
what stops a route with no vehicle (the FK is `ON DELETE SET NULL`) rendering as
`undefined`.

### `OptimizationRun.result_payload` is inlined

The one large nested type, declared inline rather than broken into named
interfaces. It mirrors the backend's JSONB payload: routes, their stops, the
baseline comparison, and the diagnostics (`matrix_source`, `plan_source`,
`solver_plan`).

`plan_source` is the field worth knowing: `"baseline"` means the solver failed
to beat the greedy plan within its budget, and `RoutePlanner` renders an
explicit warning when it sees that rather than quietly showing a 0% gain.

Being inline makes the whole payload visible in one place, at the cost of the
inner shapes not being reusable — `PlannedStop` cannot be referenced elsewhere.
Fine while only one screen reads it.

### `WsEvent` is deliberately loose

```ts
export interface WsEvent {
  type: string;
  data: any;
}
```

`data: any`, and it is the weakest type in the folder. The backend emits
thirteen event types with thirteen different payloads (catalogued in
`backend/app/websocket/README.md`), so a faithful type would be a discriminated
union on `type`. That would make `LiveOps`'s big `switch` exhaustively checked —
currently a typo in `e.data.vehicle_id` compiles fine.

It is the highest-value typing improvement available here.

---

## Adding a type

Mirror the backend's Pydantic model in `backend/app/schemas/`, field for field,
including nullability. Then use it as the type argument in
`src/api/endpoints.ts` so the shape reaches the calling component.
