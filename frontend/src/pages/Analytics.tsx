import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { PageHeader } from "../components/Layout";
import { KpiCard, Skeleton } from "../components/ui";
import { analyticsApi } from "../api/endpoints";

export default function Analytics() {
  const summary = useQuery({ queryKey: ["an-summary"], queryFn: () => analyticsApi.summary() });
  const savings = useQuery({ queryKey: ["an-savings"], queryFn: analyticsApi.optimizationSavings });
  const distByVeh = useQuery({ queryKey: ["an-dist"], queryFn: analyticsApi.distanceByVehicle });
  const overTime = useQuery({ queryKey: ["an-time"], queryFn: analyticsApi.deliveriesOverTime });
  const s = summary.data;

  return (
    <div>
      <PageHeader title="Analytics" subtitle="Fleet performance & optimization impact" />
      <div className="space-y-5 p-6">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {!s ? (
            Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-20" />)
          ) : (
            <>
              <KpiCard label="Avg route distance" value={`${s.avg_route_distance_km} km`} />
              <KpiCard label="Avg route duration" value={`${s.avg_route_duration_minutes} min`} />
              <KpiCard label="Completed routes" value={s.completed_routes} />
              <KpiCard label="Deliveries / vehicle" value={s.deliveries_per_vehicle} />
              <KpiCard label="Vehicle utilisation" value={`${s.vehicle_utilisation_pct}%`} accent="text-brand-600" />
              <KpiCard label="Capacity utilisation" value={`${s.capacity_utilisation_pct}%`} />
              <KpiCard label="Unassigned rate" value={`${s.unassigned_order_rate_pct}%`} accent="text-amber-600" />
              <KpiCard label="Avg optimization gain" value={`${s.avg_optimization_improvement_pct}%`} accent="text-green-600" />
            </>
          )}
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="card p-4">
            <h3 className="mb-3 text-sm font-semibold">Optimization savings (distance)</h3>
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={savings.data || []}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eef0f4" />
                <XAxis dataKey="run_id" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Area type="monotone" dataKey="before_km" stroke="#94a3b8" fill="#e2e8f0" name="Baseline km" />
                <Area type="monotone" dataKey="after_km" stroke="#2f66f6" fill="#bcd2ff" name="Optimized km" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="card p-4">
            <h3 className="mb-3 text-sm font-semibold">Improvement % per run</h3>
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={savings.data || []}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eef0f4" />
                <XAxis dataKey="run_id" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Line type="monotone" dataKey="improvement_pct" stroke="#16a34a" name="Improvement %" />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="card p-4 lg:col-span-2">
            <h3 className="mb-3 text-sm font-semibold">Distance by vehicle</h3>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={distByVeh.data || []}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eef0f4" />
                <XAxis dataKey="vehicle" tick={{ fontSize: 10 }} interval={0} angle={-30} textAnchor="end" height={60} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="distance_km" fill="#2f66f6" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
}
