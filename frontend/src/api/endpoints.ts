/**
 * One typed object per backend resource.
 *
 * This is the only file that knows URLs. A page calls `orderApi.cancel(7)` and
 * never sees a path, a method or a header — those are this layer's business,
 * and the transport is client.ts's.
 *
 * The type argument on each call (`apiRequest<Paginated<Order>>`) is what
 * carries the response shape out to the calling component. It is an assertion,
 * not a runtime check: if the backend renames a field, TypeScript stays happy
 * and the value becomes undefined at the point of use. Keeping src/types/ in
 * step with backend/app/schemas/ is manual — see src/types/README.md.
 */
import { apiRequest } from "./client";
import type {
  DashboardSummary,
  Depot,
  Objective,
  Order,
  OptimizationRun,
  Paginated,
  Route,
  User,
  Vehicle,
} from "../types";

// --- Auth ---
export const authApi = {
  // `form: true` and the field name `username` are both required by the OAuth2
  // password flow the backend implements — it is an email, but the spec fixes
  // the field name.
  login: (email: string, password: string) =>
    apiRequest<{ access_token: string }>("/auth/login", {
      method: "POST",
      form: true,
      body: { username: email, password },
    }),
  // Validates a stored token as a side effect of fetching the user, which is
  // how auth.loadUser() decides whether a session is still good.
  me: () => apiRequest<User>("/auth/me"),
  // Unused by the UI — there is no sign-up screen, accounts come from the seed
  // script. Kept because the endpoint exists.
  register: (body: { name: string; email: string; password: string; role?: string }) =>
    apiRequest<User>("/auth/register", { method: "POST", body }),
};

// --- Depots ---
export const depotApi = {
  list: () => apiRequest<Depot[]>("/depots"),
  create: (body: Partial<Depot>) => apiRequest<Depot>("/depots", { method: "POST", body }),
  // Unused by the UI: the Depots screen creates but does not edit.
  update: (id: number, body: Partial<Depot>) =>
    apiRequest<Depot>(`/depots/${id}`, { method: "PATCH", body }),
};

// --- Orders ---
export const orderApi = {
  // `params` is passed straight through from the page's filter state; client.ts
  // drops the empty ones, so an unselected dropdown simply does not appear in
  // the query string.
  list: (params: Record<string, any>) =>
    apiRequest<Paginated<Order>>("/orders", { params }),
  // Unused: the table already holds every field the detail view would show.
  get: (id: number) => apiRequest<Order>(`/orders/${id}`),
  create: (body: Partial<Order>) => apiRequest<Order>("/orders", { method: "POST", body }),
  // Partial<Order> because the backend is PATCH — only the fields sent change.
  update: (id: number, body: Partial<Order>) =>
    apiRequest<Order>(`/orders/${id}`, { method: "PATCH", body }),
  // A state transition, not a delete: the order and its history are kept.
  cancel: (id: number) => apiRequest<Order>(`/orders/${id}/cancel`, { method: "POST" }),
  // `remove`, not `delete` — `delete` is a reserved word and cannot be a bare
  // method name here. Returns void: the backend replies with a message body the
  // UI does not use.
  remove: (id: number) => apiRequest<void>(`/orders/${id}`, { method: "DELETE" }),
  // Unused by the UI. The PostGIS radius search has no screen yet.
  nearby: (latitude: number, longitude: number, radius_km: number) =>
    apiRequest<(Order & { distance_km: number })[]>("/orders/nearby", {
      params: { latitude, longitude, radius_km },
    }),
};

// --- Vehicles ---
export const vehicleApi = {
  // Defaulted to `{}` so callers can omit filters entirely, unlike orderApi.list.
  list: (params: Record<string, any> = {}) => apiRequest<Vehicle[]>("/vehicles", { params }),
  get: (id: number) => apiRequest<Vehicle>(`/vehicles/${id}`), // unused
  create: (body: Partial<Vehicle>) => apiRequest<Vehicle>("/vehicles", { method: "POST", body }),
  update: (id: number, body: Partial<Vehicle>) =>
    apiRequest<Vehicle>(`/vehicles/${id}`, { method: "PATCH", body }),
  // Unused. Note this one is a full table scan on the backend — vehicles have
  // no spatial index (see backend/app/geospatial/README.md).
  nearby: (latitude: number, longitude: number, radius_km: number, only_available = false) =>
    apiRequest<(Vehicle & { distance_km: number })[]>("/vehicles/nearby", {
      params: { latitude, longitude, radius_km, only_available },
    }),
};

// --- Routes ---
// Read-only, mirroring the backend: a route cannot be created directly, only by
// accepting an optimization plan.
export const routeApi = {
  // `status` may be undefined, which client.ts then omits — so one method
  // serves both "all routes" and "only ACTIVE ones".
  list: (status?: string) => apiRequest<Route[]>("/routes", { params: { status } }),
  get: (id: number) => apiRequest<Route>(`/routes/${id}`), // unused: list already includes stops
};

// --- Optimization ---
interface OptimizationRequestBody {
  depot_id: number;
  // Both lists NARROW the problem; empty means "everything eligible at this
  // depot". They cannot widen it — the backend still filters to PENDING orders
  // and AVAILABLE vehicles regardless.
  order_ids: number[];
  vehicle_ids: number[];
  objective: Objective;
}

export const optimizationApi = {
  /** Solve inline and block until done. Kept for scripts and tests. */
  // Unused by the UI, which uses startJob below. Note the comment above is
  // aspirational — the frontend has no scripts or tests; this is simply the
  // synchronous alternative, capped at the backend's much shorter request-bound
  // time budget.
  run: (body: OptimizationRequestBody) =>
    apiRequest<OptimizationRun>("/optimization/run", { method: "POST", body }),
  /** Queue a background solve; returns a PROCESSING run to poll or watch over WS. */
  // The one RoutePlanner uses. Returning immediately is what lets the solver
  // spend minutes rather than the seconds a request can be held open.
  startJob: (body: OptimizationRequestBody) =>
    apiRequest<OptimizationRun>("/optimization/jobs", { method: "POST", body }),
  runs: () => apiRequest<OptimizationRun[]>("/optimization/runs"),
  // Polled by RoutePlanner until status leaves PROCESSING.
  get: (id: number) => apiRequest<OptimizationRun>(`/optimization/runs/${id}`),
  // THE commit point: turns a stored plan into live routes. Returns the created
  // routes, so the UI can confirm what was dispatched.
  accept: (id: number) => apiRequest<Route[]>(`/optimization/runs/${id}/accept`, { method: "POST" }),
  discard: (id: number) =>
    apiRequest<{ message: string }>(`/optimization/runs/${id}/discard`, { method: "POST" }),
};

// --- Simulation ---
// NOTE: every method here returns `any`. These endpoints reply with
// loosely-shaped control/diagnostic payloads that have no interface in
// src/types/, so their responses are entirely unchecked — the weakest typing in
// the codebase, and the obvious place to tighten if the shapes settle.
export const simulationApi = {
  status: () => apiRequest<any>("/simulation/status"),
  // May resolve with `{started: false, reason}` rather than rejecting — "there
  // are no routes to simulate" is an expected outcome, not an error, so LiveOps
  // checks the flag in onSuccess.
  start: (speed_multiplier: number) =>
    apiRequest<any>("/simulation/start", { method: "POST", body: { speed_multiplier } }),
  stop: () => apiRequest<any>("/simulation/stop", { method: "POST" }),
  // Changes the clock rate mid-run — distinct from `traffic` below, which
  // changes how fast a single vehicle moves.
  speed: (speed_multiplier: number) =>
    apiRequest<any>("/simulation/speed", { method: "POST", body: { speed_multiplier } }),
  // severity: clear | moderate | severe | breakdown. A bare string, validated
  // only by the backend — an invalid value comes back as a 422.
  traffic: (route_id: number, severity: string) =>
    apiRequest<any>("/simulation/traffic", { method: "POST", body: { route_id, severity } }),
  // Re-plans only the undelivered stops, from the vehicle's live position.
  reoptimize: (route_id: number) =>
    apiRequest<any>(`/simulation/routes/${route_id}/reoptimize`, { method: "POST" }),
};

// --- Dashboard & Analytics ---
export const dashboardApi = {
  summary: () => apiRequest<DashboardSummary>("/dashboard/summary"),
  // `any[]`: the activity feed carries a free-form JSONB metadata blob per
  // event, so there is no fixed shape to declare.
  activity: () => apiRequest<any[]>("/dashboard/activity"),
};

export const analyticsApi = {
  summary: (params: Record<string, any> = {}) => apiRequest<any>("/analytics/summary", { params }),
  // The one analytics response with a real type — because it is the only one
  // whose shape is a simple, stable pair.
  ordersByStatus: () => apiRequest<{ status: string; count: number }[]>("/analytics/orders-by-status"),
  distanceByVehicle: () => apiRequest<any[]>("/analytics/distance-by-vehicle"),
  deliveriesOverTime: () => apiRequest<any[]>("/analytics/deliveries-over-time"),
  optimizationSavings: () => apiRequest<any[]>("/analytics/optimization-savings"),
};
