import { PageHeader } from "../components/Layout";
import { useAuth } from "../stores/auth";

export default function Settings() {
  const user = useAuth((s) => s.user);
  return (
    <div>
      <PageHeader title="Profile & Settings" />
      <div className="max-w-lg space-y-4 p-6">
        <div className="card p-5">
          <h3 className="mb-3 text-sm font-semibold">Account</h3>
          <dl className="space-y-2 text-sm">
            <Row label="Name" value={user?.name} />
            <Row label="Email" value={user?.email} />
            <Row label="Role" value={user?.role} />
            <Row label="Member since" value={user?.created_at ? new Date(user.created_at).toLocaleDateString() : "—"} />
          </dl>
        </div>
        <div className="card p-5 text-sm text-ink-500">
          <h3 className="mb-2 text-sm font-semibold text-ink-900">About RouteOS</h3>
          <p>
            RouteOS is an intelligent logistics & fleet optimization platform. Routes are computed with a
            constraint-based Vehicle Routing Problem solver (Google OR-Tools) over a PostGIS geospatial
            dataset, with backend-driven real-time simulation over WebSockets.
          </p>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="flex justify-between border-b border-ink-100 py-1.5 last:border-0">
      <dt className="text-ink-400">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
