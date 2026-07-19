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
  login: (email: string, password: string) =>
    apiRequest<{ access_token: string }>("/auth/login", {
      method: "POST",
      form: true,
      body: { username: email, password },
    }),
  me: () => apiRequest<User>("/auth/me"),
  register: (body: { name: string; email: string; password: string; role?: string }) =>
    apiRequest<User>("/auth/register", { method: "POST", body }),
};

// --- Depots ---
export const depotApi = {
  list: () => apiRequest<Depot[]>("/depots"),
  create: (body: Partial<Depot>) => apiRequest<Depot>("/depots", { method: "POST", body }),
  update: (id: number, body: Partial<Depot>) =>
    apiRequest<Depot>(`/depots/${id}`, { method: "PATCH", body }),
};

// --- Orders ---
export const orderApi = {
  list: (params: Record<string, any>) =>
    apiRequest<Paginated<Order>>("/orders", { params }),
  get: (id: number) => apiRequest<Order>(`/orders/${id}`),
  create: (body: Partial<Order>) => apiRequest<Order>("/orders", { method: "POST", body }),
  update: (id: number, body: Partial<Order>) =>
    apiRequest<Order>(`/orders/${id}`, { method: "PATCH", body }),
  cancel: (id: number) => apiRequest<Order>(`/orders/${id}/cancel`, { method: "POST" }),
  remove: (id: number) => apiRequest<void>(`/orders/${id}`, { method: "DELETE" }),
  nearby: (latitude: number, longitude: number, radius_km: number) =>
    apiRequest<(Order & { distance_km: number })[]>("/orders/nearby", {
      params: { latitude, longitude, radius_km },
    }),
};

// --- Vehicles ---
export const vehicleApi = {
  list: (params: Record<string, any> = {}) => apiRequest<Vehicle[]>("/vehicles", { params }),
  get: (id: number) => apiRequest<Vehicle>(`/vehicles/${id}`),
  create: (body: Partial<Vehicle>) => apiRequest<Vehicle>("/vehicles", { method: "POST", body }),
  update: (id: number, body: Partial<Vehicle>) =>
    apiRequest<Vehicle>(`/vehicles/${id}`, { method: "PATCH", body }),
  nearby: (latitude: number, longitude: number, radius_km: number, only_available = false) =>
    apiRequest<(Vehicle & { distance_km: number })[]>("/vehicles/nearby", {
      params: { latitude, longitude, radius_km, only_available },
    }),
};

// --- Routes ---
export const routeApi = {
  list: (status?: string) => apiRequest<Route[]>("/routes", { params: { status } }),
  get: (id: number) => apiRequest<Route>(`/routes/${id}`),
};

// --- Optimization ---
export const optimizationApi = {
  run: (body: { depot_id: number; order_ids: number[]; vehicle_ids: number[]; objective: Objective }) =>
    apiRequest<OptimizationRun>("/optimization/run", { method: "POST", body }),
  runs: () => apiRequest<OptimizationRun[]>("/optimization/runs"),
  get: (id: number) => apiRequest<OptimizationRun>(`/optimization/runs/${id}`),
  accept: (id: number) => apiRequest<Route[]>(`/optimization/runs/${id}/accept`, { method: "POST" }),
  discard: (id: number) =>
    apiRequest<{ message: string }>(`/optimization/runs/${id}/discard`, { method: "POST" }),
};

// --- Simulation ---
export const simulationApi = {
  status: () => apiRequest<any>("/simulation/status"),
  start: (speed_multiplier: number) =>
    apiRequest<any>("/simulation/start", { method: "POST", body: { speed_multiplier } }),
  stop: () => apiRequest<any>("/simulation/stop", { method: "POST" }),
  speed: (speed_multiplier: number) =>
    apiRequest<any>("/simulation/speed", { method: "POST", body: { speed_multiplier } }),
  traffic: (route_id: number, severity: string) =>
    apiRequest<any>("/simulation/traffic", { method: "POST", body: { route_id, severity } }),
  reoptimize: (route_id: number) =>
    apiRequest<any>(`/simulation/routes/${route_id}/reoptimize`, { method: "POST" }),
};

// --- Dashboard & Analytics ---
export const dashboardApi = {
  summary: () => apiRequest<DashboardSummary>("/dashboard/summary"),
  activity: () => apiRequest<any[]>("/dashboard/activity"),
};

export const analyticsApi = {
  summary: (params: Record<string, any> = {}) => apiRequest<any>("/analytics/summary", { params }),
  ordersByStatus: () => apiRequest<{ status: string; count: number }[]>("/analytics/orders-by-status"),
  distanceByVehicle: () => apiRequest<any[]>("/analytics/distance-by-vehicle"),
  deliveriesOverTime: () => apiRequest<any[]>("/analytics/deliveries-over-time"),
  optimizationSavings: () => apiRequest<any[]>("/analytics/optimization-savings"),
};
