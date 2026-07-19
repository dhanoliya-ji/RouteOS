import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../stores/auth";

const NAV = [
  { to: "/", label: "Dashboard", icon: "▚", end: true },
  { to: "/orders", label: "Orders", icon: "▤" },
  { to: "/fleet", label: "Fleet", icon: "▦" },
  { to: "/depots", label: "Depots", icon: "◈" },
  { to: "/planner", label: "Route Planner", icon: "✦" },
  { to: "/routes", label: "Active Routes", icon: "➟" },
  { to: "/live", label: "Live Operations", icon: "◉" },
  { to: "/analytics", label: "Analytics", icon: "▨" },
  { to: "/optimization", label: "Optimization Log", icon: "❋" },
];

export function Layout() {
  const { user, logout } = useAuth();
  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="flex w-60 flex-col border-r border-ink-200 bg-white">
        <div className="flex items-center gap-2 px-5 py-4">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">
            R
          </div>
          <div>
            <div className="text-sm font-semibold leading-tight">RouteOS</div>
            <div className="text-[10px] uppercase tracking-wider text-ink-400">Fleet Optimization</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 px-3 py-2">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive ? "bg-brand-50 text-brand-700" : "text-ink-600 hover:bg-ink-100"
                }`
              }
            >
              <span className="text-ink-400">{n.icon}</span>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-ink-200 p-3">
          <NavLink to="/settings" className="block rounded-lg px-3 py-2 text-sm text-ink-600 hover:bg-ink-100">
            <div className="font-medium">{user?.name}</div>
            <div className="text-[11px] text-ink-400">{user?.role}</div>
          </NavLink>
          <button className="btn-ghost mt-1 w-full" onClick={logout}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto bg-ink-50">
        <Outlet />
      </main>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between border-b border-ink-200 bg-white px-6 py-4">
      <div>
        <h1 className="text-lg font-semibold">{title}</h1>
        {subtitle && <p className="text-sm text-ink-400">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
