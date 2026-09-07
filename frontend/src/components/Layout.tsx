// The application shell and the shared page header.
//
// Presentational only — neither component fetches anything. Layout reads the
// auth store purely to render the user's name and the sign-out button.
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../stores/auth";

// The single source of the sidebar. Adding a screen means one entry here plus
// one <Route> in App.tsx — nothing else.
const NAV = [
  // `end: true` is REQUIRED on this one. Its path is "/", which is a prefix of
  // every other route, and NavLink matches by prefix by default — so without
  // `end` the Dashboard link would render as active on every single page.
  { to: "/", label: "Dashboard", icon: "▚", end: true },
  { to: "/orders", label: "Orders", icon: "▤" },
  { to: "/fleet", label: "Fleet", icon: "▦" },
  { to: "/depots", label: "Depots", icon: "◈" },
  { to: "/planner", label: "Route Planner", icon: "✦" },
  { to: "/routes", label: "Active Routes", icon: "➟" },
  { to: "/live", label: "Live Operations", icon: "◉" },
  { to: "/analytics", label: "Analytics", icon: "▨" },
  { to: "/optimization", label: "Optimization Log", icon: "❋" },
  // Note /settings is deliberately absent — it is reached from the user block
  // at the bottom of the sidebar instead of the main nav.
];

/**
 * The frame every signed-in screen renders inside.
 *
 * Mounted ONCE by App.tsx as the parent route, so navigating between screens
 * swaps only the <Outlet/> content — the sidebar is never remounted, which is
 * why moving around does not flash the shell.
 */
export function Layout() {
  const { user, logout } = useAuth();
  return (
    // h-screen + overflow-hidden here, with overflow-y-auto on <main> below, is
    // what pins the sidebar while the content scrolls. It is also what gives
    // the map screens a bounded height to fill: RoutePlanner and LiveOps rely
    // on this, since Leaflet needs a container with a real height.
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

        {/* flex-1 pushes the user block below to the bottom of the sidebar. */}
        <nav className="flex-1 space-y-0.5 px-3 py-2">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              // NavLink passes its own active state to a className function,
              // so the highlight needs no manual comparison against the
              // current location.
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
          {/* The user's name doubles as the link to Settings. */}
          <NavLink to="/settings" className="block rounded-lg px-3 py-2 text-sm text-ink-600 hover:bg-ink-100">
            {/* Optional chaining because Layout can render for the instant
                before the auth store settles. */}
            <div className="font-medium">{user?.name}</div>
            <div className="text-[11px] text-ink-400">{user?.role}</div>
          </NavLink>
          {/* No confirmation: logout only drops a local token, so it costs
              nothing to undo by signing in again. */}
          <button className="btn-ghost mt-1 w-full" onClick={logout}>
            Sign out
          </button>
        </div>
      </aside>

      {/* The only scrolling region. <Outlet/> is where the matched child route
          renders — see the nested routes in App.tsx. */}
      <main className="flex-1 overflow-y-auto bg-ink-50">
        <Outlet />
      </main>
    </div>
  );
}

/**
 * The title bar at the top of every screen.
 *
 * Used by all ten pages, which is what makes them look like one application.
 * Lives here rather than in ui.tsx because it is part of the page frame rather
 * than a reusable widget.
 *
 * `subtitle` is a ReactNode, not a string, so a page can pass markup — LiveOps
 * puts its live connection indicator there.
 */
export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between border-b border-ink-200 bg-white px-6 py-4">
      <div>
        <h1 className="text-lg font-semibold">{title}</h1>
        {subtitle && <p className="text-sm text-ink-400">{subtitle}</p>}
      </div>
      {/* Right-aligned action slot. Pages pass `editable && <button/>`, so a
          VIEWER simply gets nothing here. */}
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
