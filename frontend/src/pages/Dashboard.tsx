// The landing screen: KPI tiles, three charts and a live activity feed.
//
// Five independent queries rather than one aggregate call, so each panel loads
// and refetches on its own and a slow chart cannot hold up the tiles.
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { PageHeader } from "../components/Layout";
import { KpiCard, Skeleton, EmptyState } from "../components/ui";
import { analyticsApi, dashboardApi } from "../api/endpoints";

// Chart colours for the status pie.
//
// A SEPARATE palette from the one in components/ui.tsx: that one is Tailwind
// background/text class pairs for badges, these are raw hex values because
// Recharts needs a colour string, not a class. The two are kept semantically
// in step by hand - amber for pending, green for delivered, and so on.
const STATUS_COLORS: Record<string, string> = {
  PENDING: "#f59e0b",
  ASSIGNED: "#3b82f6",
  OUT_FOR_DELIVERY: "#6366f1",
  DELIVERED: "#22c55e",
  FAILED: "#ef4444",
  CANCELLED: "#94a3b8",
};

export default function Dashboard() {
  // The two live panels poll every 8s, so the dashboard keeps up with a running
  // simulation without needing the WebSocket. The three chart queries below do
  // not poll - historical aggregates do not change second to second.
  const summary = useQuery({ queryKey: ["dash-summary"], queryFn: dashboardApi.summary, refetchInterval: 8000 });
  const activity = useQuery({ queryKey: ["dash-activity"], queryFn: dashboardApi.activity, refetchInterval: 8000 });
  const byStatus = useQuery({ queryKey: ["orders-by-status"], queryFn: analyticsApi.ordersByStatus });
  const overTime = useQuery({ queryKey: ["deliveries-over-time"], queryFn: analyticsApi.deliveriesOverTime });
  const distByVeh = useQuery({ queryKey: ["distance-by-vehicle"], queryFn: analyticsApi.distanceByVehicle });

  const s = summary.data;

  return (
    <div>
      <PageHeader title="Operations Dashboard" subtitle="Live fleet & delivery overview" />
      <div className="space-y-5 p-6">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {/* `|| !s` as well as isLoading: a cached-but-undefined result would
              otherwise fall through and throw on s.total_orders. Eight
              skeletons, one per tile, so the grid does not reflow on arrival. */}
          {summary.isLoading || !s ? (
            Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-20" />)
          ) : (
            <>
              <KpiCard label="Total Orders" value={s.total_orders} />
              <KpiCard label="Pending" value={s.pending} accent="text-amber-600" />
              <KpiCard label="Out for Delivery" value={s.out_for_delivery} accent="text-indigo-600" />
              <KpiCard label="Delivered Today" value={s.delivered_today} accent="text-green-600" />
              <KpiCard label="Active Vehicles" value={s.active_vehicles} accent="text-indigo-600" />
              <KpiCard label="Available Vehicles" value={s.available_vehicles} accent="text-green-600" />
              <KpiCard label="Distance Today" value={`${s.total_distance_today_km} km`} />
              <KpiCard label="On-time Rate" value={`${s.on_time_delivery_rate}%`} accent="text-brand-600" />
            </>
          )}
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="card p-4 lg:col-span-2">
            <h3 className="mb-3 text-sm font-semibold">Deliveries over time</h3>
            <ResponsiveContainer width="100%" height={240}>
              {/* `|| []` on every chart: Recharts throws on undefined data, and
                  these queries have no isLoading branch of their own - they
                  render as an empty chart until the data lands. */}
              <LineChart data={overTime.data || []}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eef0f4" />
                <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="total" stroke="#94a3b8" name="Created" />
                <Line type="monotone" dataKey="delivered" stroke="#22c55e" name="Delivered" />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="card p-4">
            <h3 className="mb-3 text-sm font-semibold">Orders by status</h3>
            <ResponsiveContainer width="100%" height={240}>
              <PieChart>
                <Pie
                  data={byStatus.data || []}
                  dataKey="count"
                  nameKey="status"
                  innerRadius={45}
                  outerRadius={80}
                  paddingAngle={2}
                >
                  {(byStatus.data || []).map((d) => (
                    <Cell key={d.status} fill={STATUS_COLORS[d.status] || "#94a3b8"} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="card p-4 lg:col-span-2">
            <h3 className="mb-3 text-sm font-semibold">Distance by vehicle</h3>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={distByVeh.data || []}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eef0f4" />
                <XAxis dataKey="vehicle" tick={{ fontSize: 10 }} interval={0} angle={-30} textAnchor="end" height={60} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="distance_km" fill="#2f66f6" name="Distance (km)" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="card p-4">
            <h3 className="mb-3 text-sm font-semibold">Recent activity</h3>
            <div className="space-y-2">
              {activity.isLoading ? (
                <Skeleton className="h-40" />
              ) : (activity.data || []).length === 0 ? (
                <EmptyState title="No activity yet" hint="Run a simulation to see live events." />
              ) : (
                // The backend already caps this at 15; sliced to 10 so the panel
                // fits without scrolling.
                (activity.data || []).slice(0, 10).map((e) => (
                  <div key={e.id} className="flex items-start gap-2 border-b border-ink-100 pb-2 text-sm last:border-0">
                    <span className="mt-0.5 text-[10px] text-ink-400">
                      {new Date(e.created_at).toLocaleTimeString()}
                    </span>
                    <span className="text-ink-700">
                      {e.event_type.replaceAll("_", " ").toLowerCase()}
                      {e.order_id ? ` · order #${e.order_id}` : ""}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
