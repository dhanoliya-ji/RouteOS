export type Role = "ADMIN" | "DISPATCHER" | "VIEWER";

export interface User {
  id: number;
  name: string;
  email: string;
  role: Role;
  is_active: boolean;
  created_at: string;
}

export type OrderStatus =
  | "PENDING" | "ASSIGNED" | "OUT_FOR_DELIVERY" | "DELIVERED" | "FAILED" | "CANCELLED";
export type Priority = "LOW" | "NORMAL" | "HIGH" | "URGENT";
export type VehicleStatus =
  | "AVAILABLE" | "ASSIGNED" | "IN_TRANSIT" | "MAINTENANCE" | "OFFLINE";
export type RouteStatus = "PLANNED" | "ACTIVE" | "COMPLETED" | "CANCELLED";
export type Objective = "MIN_DISTANCE" | "MIN_TIME" | "BALANCED";

export interface Depot {
  id: number;
  name: string;
  address?: string | null;
  latitude: number;
  longitude: number;
  operating_start: string;
  operating_end: string;
}

export interface Order {
  id: number;
  order_number: string;
  customer_name: string;
  customer_phone?: string | null;
  delivery_address: string;
  latitude: number;
  longitude: number;
  weight_kg: number;
  volume?: number | null;
  priority: Priority;
  status: OrderStatus;
  delivery_window_start?: string | null;
  delivery_window_end?: string | null;
  service_time_minutes: number;
  depot_id: number;
  created_at: string;
}

export interface Vehicle {
  id: number;
  registration_number: string;
  driver_name: string;
  vehicle_type: string;
  capacity_kg: number;
  capacity_volume?: number | null;
  current_load_kg: number;
  status: VehicleStatus;
  current_latitude?: number | null;
  current_longitude?: number | null;
  home_depot_id: number;
  max_route_distance_km?: number | null;
}

export interface RouteStop {
  id: number;
  order_id: number;
  stop_sequence: number;
  latitude: number;
  longitude: number;
  estimated_arrival?: string | null;
  actual_arrival?: string | null;
  distance_from_previous_km: number;
  status: string;
}

export interface Route {
  id: number;
  route_code: string;
  vehicle_id: number | null;
  depot_id: number;
  status: RouteStatus;
  total_distance_km: number;
  estimated_duration_minutes: number;
  actual_duration_minutes?: number | null;
  optimization_score?: number | null;
  total_load_kg: number;
  progress_stop_index: number;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
  stops: RouteStop[];
}

export interface Metrics {
  total_distance_km: number;
  estimated_duration_minutes: number;
  vehicles_used: number;
  assigned_orders: number;
  unassigned_orders: number;
}

export interface Comparison {
  baseline: Metrics;
  optimized: Metrics;
  distance_reduction_pct: number;
  time_reduction_pct: number;
  vehicles_reduction_pct: number;
}

export interface OptimizationRun {
  id: number;
  status: "PENDING" | "PROCESSING" | "COMPLETED" | "FAILED";
  algorithm: string;
  objective: Objective;
  depot_id: number | null;
  orders_count: number;
  vehicles_count: number;
  assigned_count: number;
  unassigned_count: number;
  total_distance_before: number | null;
  total_distance_after: number | null;
  improvement_percentage: number | null;
  execution_time_ms: number | null;
  objective_value: number | null;
  error_message: string | null;
  created_at: string;
  result_payload?: {
    routes: {
      vehicle_id: number;
      registration_number: string;
      total_distance_km: number;
      estimated_duration_minutes: number;
      total_load_kg: number;
      capacity_kg: number;
      stops: {
        order_id: number;
        order_number: string;
        stop_sequence: number;
        latitude: number;
        longitude: number;
        distance_from_previous_km: number;
        load_kg: number;
        eta_minutes_from_start: number;
      }[];
    }[];
    unassigned: { order_id: number; order_number: string; reason: string }[];
    comparison: Comparison;
    objective_value: number;
    execution_time_ms: number;
    matrix_source: string;
    /** Which heuristic produced the dispatched plan. "baseline" means the
     *  solver did not beat greedy within its time budget. */
    plan_source?: "solver" | "baseline";
    solver_plan?: { total_distance_km: number; vehicles_used: number };
  } | null;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface DashboardSummary {
  total_orders: number;
  pending: number;
  out_for_delivery: number;
  delivered_today: number;
  active_vehicles: number;
  available_vehicles: number;
  total_distance_today_km: number;
  active_routes: number;
  on_time_delivery_rate: number;
}

export interface WsEvent {
  type: string;
  data: any;
}
