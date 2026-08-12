import { useMemo, useState } from "react";
import { MapContainer, Marker, Polyline, Popup, TileLayer } from "react-leaflet";
import { useMutation, useQuery } from "@tanstack/react-query";
import { PageHeader } from "../components/Layout";
import { EmptyState, Skeleton, StatusBadge } from "../components/ui";
import { depotApi, optimizationApi, orderApi, vehicleApi } from "../api/endpoints";
import { useToast } from "../stores/toast";
import { NCR_CENTER, ROUTE_COLORS, coloredDot, depotIcon } from "../utils/map";
import type { Objective, OptimizationRun } from "../types";

export default function RoutePlanner() {
  const push = useToast((s) => s.push);
  const [objective, setObjective] = useState<Objective>("BALANCED");
  const [selOrders, setSelOrders] = useState<Set<number>>(new Set());
  const [selVehicles, setSelVehicles] = useState<Set<number>>(new Set());
  const [run, setRun] = useState<OptimizationRun | null>(null);

  const depots = useQuery({ queryKey: ["depots"], queryFn: depotApi.list });
  const depot = depots.data?.[0];
  const orders = useQuery({
    queryKey: ["planner-orders"],
    queryFn: () => orderApi.list({ status: "PENDING", page_size: 200 }),
  });
  const vehicles = useQuery({ queryKey: ["planner-vehicles"], queryFn: () => vehicleApi.list({ status: "AVAILABLE" }) });

  const runMut = useMutation({
    mutationFn: () =>
      optimizationApi.run({
        depot_id: depot!.id,
        order_ids: [...selOrders],
        vehicle_ids: [...selVehicles],
        objective,
      }),
    onSuccess: (r) => {
      setRun(r);
      push(`Optimization complete in ${r.execution_time_ms} ms`, "success");
    },
    onError: (e: any) => push(e.message, "error"),
  });

  const acceptMut = useMutation({
    mutationFn: (id: number) => optimizationApi.accept(id),
    onSuccess: () => {
      push("Plan accepted — routes are now active", "success");
      setRun(null);
      setSelOrders(new Set());
      setSelVehicles(new Set());
      orders.refetch();
      vehicles.refetch();
    },
    onError: (e: any) => push(e.message, "error"),
  });

  const toggle = (set: Set<number>, id: number, setter: (s: Set<number>) => void) => {
    const next = new Set(set);
    next.has(id) ? next.delete(id) : next.add(id);
    setter(next);
  };

  const payload = run?.result_payload;
  const polylines = useMemo(() => {
    if (!payload || !depot) return [];
    return payload.routes.map((r, i) => ({
      color: ROUTE_COLORS[i % ROUTE_COLORS.length],
      positions: [
        [depot.latitude, depot.longitude],
        ...r.stops.map((s) => [s.latitude, s.longitude] as [number, number]),
        [depot.latitude, depot.longitude],
      ] as [number, number][],
      reg: r.registration_number,
    }));
  }, [payload, depot]);

  const comp = payload?.comparison;

  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Route Planner" subtitle="Select orders & vehicles, then optimize with OR-Tools" />
      <div className="grid flex-1 grid-cols-12 gap-0 overflow-hidden">
        {/* LEFT: pending orders */}
        <div className="col-span-3 flex flex-col overflow-hidden border-r border-ink-200 bg-white">
          <div className="flex items-center justify-between px-4 py-3">
            <h3 className="text-sm font-semibold">Pending Orders</h3>
            <button
              className="text-xs text-brand-600 hover:underline"
              onClick={() =>
                setSelOrders(new Set((orders.data?.items || []).slice(0, 50).map((o) => o.id)))
              }
            >
              Select 50
            </button>
          </div>
          <div className="flex-1 space-y-1 overflow-y-auto px-3 pb-3">
            {orders.isLoading ? (
              <Skeleton className="h-40" />
            ) : (
              (orders.data?.items || []).map((o) => (
                <label key={o.id} className={`flex cursor-pointer items-center gap-2 rounded-lg border px-2 py-1.5 text-sm ${selOrders.has(o.id) ? "border-brand-400 bg-brand-50" : "border-transparent hover:bg-ink-50"}`}>
                  <input type="checkbox" checked={selOrders.has(o.id)} onChange={() => toggle(selOrders, o.id, setSelOrders)} />
                  <span className="flex-1 truncate">{o.order_number}</span>
                  <span className="text-xs text-ink-400">{o.weight_kg}kg</span>
                  <StatusBadge value={o.priority} />
                </label>
              ))
            )}
          </div>
          <div className="border-t border-ink-200 px-4 py-2 text-xs text-ink-500">{selOrders.size} selected</div>
        </div>

        {/* CENTER: map */}
        <div className="col-span-6 relative">
          <MapContainer center={NCR_CENTER} zoom={11} className="h-full">
            <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="© OpenStreetMap" />
            {depot && <Marker position={[depot.latitude, depot.longitude]} icon={depotIcon}><Popup>{depot.name}</Popup></Marker>}
            {polylines.map((p, i) => (
              <Polyline key={i} positions={p.positions} pathOptions={{ color: p.color, weight: 4, opacity: 0.8 }} />
            ))}
            {payload
              ? payload.routes.flatMap((r, ri) =>
                  r.stops.map((s) => (
                    <Marker key={`${ri}-${s.order_id}`} position={[s.latitude, s.longitude]} icon={coloredDot(ROUTE_COLORS[ri % ROUTE_COLORS.length])}>
                      <Popup>{s.order_number} · stop {s.stop_sequence} · {r.registration_number}</Popup>
                    </Marker>
                  ))
                )
              : (orders.data?.items || [])
                  .filter((o) => selOrders.has(o.id))
                  .map((o) => <Marker key={o.id} position={[o.latitude, o.longitude]} icon={coloredDot("#94a3b8", 10)} />)}
          </MapContainer>
        </div>

        {/* RIGHT: controls + result */}
        <div className="col-span-3 flex flex-col overflow-y-auto border-l border-ink-200 bg-white">
          <div className="space-y-3 border-b border-ink-200 p-4">
            <h3 className="text-sm font-semibold">Available Vehicles</h3>
            <div className="max-h-40 space-y-1 overflow-y-auto">
              {(vehicles.data || []).map((v) => (
                <label key={v.id} className={`flex cursor-pointer items-center gap-2 rounded-lg border px-2 py-1.5 text-sm ${selVehicles.has(v.id) ? "border-brand-400 bg-brand-50" : "border-transparent hover:bg-ink-50"}`}>
                  <input type="checkbox" checked={selVehicles.has(v.id)} onChange={() => toggle(selVehicles, v.id, setSelVehicles)} />
                  <span className="flex-1 truncate">{v.registration_number}</span>
                  <span className="text-xs text-ink-400">{v.capacity_kg}kg</span>
                </label>
              ))}
            </div>
            <button className="text-xs text-brand-600 hover:underline" onClick={() => setSelVehicles(new Set((vehicles.data || []).map((v) => v.id)))}>
              Select all vehicles
            </button>
            <div>
              <label className="label">Objective</label>
              <select className="input" value={objective} onChange={(e) => setObjective(e.target.value as Objective)}>
                <option value="BALANCED">Balanced</option>
                <option value="MIN_DISTANCE">Minimize distance</option>
                <option value="MIN_TIME">Minimize time</option>
              </select>
            </div>
            <button
              className="btn-primary w-full"
              disabled={runMut.isPending || selOrders.size === 0 || selVehicles.size === 0 || !depot}
              onClick={() => runMut.mutate()}
            >
              {runMut.isPending ? "Optimizing…" : "⚙ Optimize Routes"}
            </button>
          </div>

          <div className="flex-1 p-4">
            {runMut.isPending && <Skeleton className="h-40" />}
            {!run && !runMut.isPending && <EmptyState title="No plan yet" hint="Select orders + vehicles and optimize." />}
            {run && comp && (
              <div className="space-y-3">
                <h3 className="text-sm font-semibold">Optimized Plan</h3>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <Metric label="Routes" value={comp.optimized.vehicles_used} />
                  <Metric label="Distance" value={`${comp.optimized.total_distance_km} km`} />
                  <Metric label="Duration" value={`${Math.round(comp.optimized.estimated_duration_minutes)} min`} />
                  <Metric label="Assigned" value={comp.optimized.assigned_orders} />
                </div>
                <div className="rounded-lg bg-green-50 p-3 text-sm">
                  <div className="font-medium text-green-800">vs. naive baseline</div>
                  <div className="mt-1 grid grid-cols-3 gap-1 text-center text-xs">
                    <div><div className="text-lg font-semibold text-green-700">{comp.distance_reduction_pct}%</div>distance</div>
                    <div><div className="text-lg font-semibold text-green-700">{comp.time_reduction_pct}%</div>time</div>
                    <div><div className="text-lg font-semibold text-green-700">{comp.vehicles_reduction_pct}%</div>vehicles</div>
                  </div>
                  <div className="mt-2 text-[11px] text-green-700">
                    Baseline: {comp.baseline.total_distance_km} km / {comp.baseline.vehicles_used} vehicles
                  </div>
                </div>
                {payload!.unassigned.length > 0 && (
                  <div className="rounded-lg bg-amber-50 p-2 text-xs text-amber-800">
                    {payload!.unassigned.length} unassigned:{" "}
                    {payload!.unassigned.slice(0, 3).map((u) => `${u.order_number} (${u.reason})`).join(", ")}
                    {payload!.unassigned.length > 3 ? "…" : ""}
                  </div>
                )}
                <div className="flex gap-2">
                  <button className="btn-primary flex-1" disabled={acceptMut.isPending} onClick={() => acceptMut.mutate(run.id)}>
                    Accept Plan
                  </button>
                  <button className="btn-ghost flex-1" onClick={() => setRun(null)}>Discard</button>
                </div>
                {payload!.plan_source === "baseline" && (
                  // Be explicit rather than quietly presenting a 0% gain: the
                  // solver ran out of time budget and the greedy plan won.
                  <div className="rounded-lg bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-800">
                    <b>Baseline plan dispatched.</b> The solver did not beat the nearest-neighbour
                    plan within its {Math.round(run.execution_time_ms! / 1000)}s budget
                    {payload!.solver_plan
                      ? ` (its best was ${payload!.solver_plan.total_distance_km} km on ${payload!.solver_plan.vehicles_used} vehicles)`
                      : ""}
                    , so the better plan was kept. Raise <code>SOLVER_TIME_LIMIT_SECONDS</code> or
                    give the service more CPU to improve on this.
                  </div>
                )}
                <div className="text-[11px] text-ink-400">
                  Solver: {payload!.matrix_source} matrix · objective {run.objective} ·{" "}
                  {run.execution_time_ms} ms
                  {payload!.plan_source ? ` · plan: ${payload!.plan_source}` : ""}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-lg bg-ink-50 p-2">
      <div className="text-[10px] uppercase text-ink-400">{label}</div>
      <div className="font-semibold">{value}</div>
    </div>
  );
}
