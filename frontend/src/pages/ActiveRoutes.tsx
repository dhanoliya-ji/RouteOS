import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "../components/Layout";
import { EmptyState, Skeleton, StatusBadge } from "../components/ui";
import { routeApi } from "../api/endpoints";
import type { Route } from "../types";

export default function ActiveRoutes() {
  const [status, setStatus] = useState("");
  const routes = useQuery({ queryKey: ["routes", status], queryFn: () => routeApi.list(status || undefined), refetchInterval: 6000 });
  const [expanded, setExpanded] = useState<number | null>(null);

  return (
    <div>
      <PageHeader
        title="Routes"
        subtitle="Planned, active & completed routes"
        actions={
          <select className="input py-1.5" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            <option value="PLANNED">Planned</option>
            <option value="ACTIVE">Active</option>
            <option value="COMPLETED">Completed</option>
          </select>
        }
      />
      <div className="space-y-3 p-6">
        {routes.isLoading ? (
          <Skeleton className="h-64" />
        ) : (routes.data || []).length === 0 ? (
          <EmptyState title="No routes" hint="Accept a plan in the Route Planner to create routes." />
        ) : (
          (routes.data || []).map((r: Route) => (
            <div key={r.id} className="card p-4">
              <div className="flex cursor-pointer items-center justify-between" onClick={() => setExpanded(expanded === r.id ? null : r.id)}>
                <div className="flex items-center gap-3">
                  <span className="font-semibold">{r.route_code}</span>
                  <StatusBadge value={r.status} />
                </div>
                <div className="flex gap-5 text-sm text-ink-500">
                  <span>{r.stops.length} stops</span>
                  <span>{r.total_distance_km} km</span>
                  <span>{Math.round(r.estimated_duration_minutes)} min</span>
                  <span>{r.total_load_kg} kg</span>
                  {r.optimization_score != null && <span className="text-green-600">−{r.optimization_score}%</span>}
                </div>
              </div>
              {expanded === r.id && (
                <div className="mt-3 border-t border-ink-100 pt-3">
                  <table className="w-full text-sm">
                    <thead className="text-left text-xs uppercase text-ink-400">
                      <tr><th className="py-1">#</th><th>Order</th><th>Dist from prev</th><th>ETA</th><th>Status</th></tr>
                    </thead>
                    <tbody>
                      {[...r.stops].sort((a, b) => a.stop_sequence - b.stop_sequence).map((s) => (
                        <tr key={s.id} className="border-t border-ink-50">
                          <td className="py-1">{s.stop_sequence}</td>
                          <td>#{s.order_id}</td>
                          <td>{s.distance_from_previous_km} km</td>
                          <td>{s.estimated_arrival ? new Date(s.estimated_arrival).toLocaleTimeString() : "—"}</td>
                          <td><StatusBadge value={s.status} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
