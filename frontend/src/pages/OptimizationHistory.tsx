import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "../components/Layout";
import { EmptyState, Skeleton, StatusBadge } from "../components/ui";
import { optimizationApi } from "../api/endpoints";

export default function OptimizationHistory() {
  const runs = useQuery({ queryKey: ["opt-runs"], queryFn: optimizationApi.runs });

  return (
    <div>
      <PageHeader title="Optimization Log" subtitle="History of solver runs & measured savings" />
      <div className="p-6">
        {runs.isLoading ? (
          <Skeleton className="h-64" />
        ) : (runs.data || []).length === 0 ? (
          <EmptyState title="No optimization runs yet" hint="Run the Route Planner to create one." />
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-ink-50 text-left text-xs uppercase text-ink-400">
                <tr>
                  <th className="px-4 py-2.5">Run</th>
                  <th className="px-4 py-2.5">Objective</th>
                  <th className="px-4 py-2.5">Status</th>
                  <th className="px-4 py-2.5">Orders</th>
                  <th className="px-4 py-2.5">Assigned</th>
                  <th className="px-4 py-2.5">Before → After</th>
                  <th className="px-4 py-2.5">Improvement</th>
                  <th className="px-4 py-2.5">Time</th>
                </tr>
              </thead>
              <tbody>
                {(runs.data || []).map((r) => (
                  <tr key={r.id} className="border-t border-ink-100">
                    <td className="px-4 py-2.5 font-medium">#{r.id}</td>
                    <td className="px-4 py-2.5">{r.objective}</td>
                    <td className="px-4 py-2.5"><StatusBadge value={r.status} /></td>
                    <td className="px-4 py-2.5">{r.orders_count}</td>
                    <td className="px-4 py-2.5">{r.assigned_count}/{r.orders_count}</td>
                    <td className="px-4 py-2.5">
                      {r.total_distance_before != null ? `${r.total_distance_before} → ${r.total_distance_after} km` : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-green-600">{r.improvement_percentage != null ? `${r.improvement_percentage}%` : "—"}</td>
                    <td className="px-4 py-2.5 text-ink-400">{r.execution_time_ms} ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
