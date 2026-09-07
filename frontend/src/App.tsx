// The route table and the authentication gate.
//
// Two responsibilities and no rendering of its own: decide which screen a URL
// maps to, and refuse every screen but /login until we know who is signed in.
import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./stores/auth";
import { ToastHost } from "./components/ui";
import { Layout } from "./components/Layout";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Orders from "./pages/Orders";
import Fleet from "./pages/Fleet";
import Depots from "./pages/Depots";
import RoutePlanner from "./pages/RoutePlanner";
import ActiveRoutes from "./pages/ActiveRoutes";
import LiveOps from "./pages/LiveOps";
import Analytics from "./pages/Analytics";
import OptimizationHistory from "./pages/OptimizationHistory";
import Settings from "./pages/Settings";

/**
 * The auth gate. Three outcomes, not two — the middle one is the point.
 *
 * On a page refresh the token is still in localStorage but the user object is
 * gone, and confirming the token takes a round trip to /auth/me. Without the
 * `loading` branch, that in-between moment looks identical to "signed out" and
 * a signed-in user gets bounced to /login on every reload.
 */
function Protected({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="p-10 text-ink-500">Loading…</div>; // don't decide yet
  if (!user) return <Navigate to="/login" replace />; // decided: signed out
  return children; // decided: signed in
}

export default function App() {
  // Select the action alone rather than destructuring the store. A Zustand
  // action is a stable reference, so the effect below runs exactly once —
  // destructuring would hand back a new object each render and re-run it.
  const loadUser = useAuth((s) => s.loadUser);
  useEffect(() => {
    // Validate any stored token on startup. This is what flips `loading` to
    // false, so the gate above can stop waiting.
    loadUser();
  }, [loadUser]);

  return (
    <>
      <Routes>
        {/* Outside the shell: no sidebar, and reachable while signed out. */}
        <Route path="/login" element={<Login />} />

        {/* Every real screen is a CHILD of this route, so the gate and the
            shell are applied once. A new screen added below is protected by
            default rather than by remembering to wrap it — Layout renders an
            <Outlet/> where the child appears. */}
        <Route
          path="/"
          element={
            <Protected>
              <Layout />
            </Protected>
          }
        >
          {/* `index` = the path itself, "/". */}
          <Route index element={<Dashboard />} />
          <Route path="orders" element={<Orders />} />
          <Route path="fleet" element={<Fleet />} />
          <Route path="depots" element={<Depots />} />
          <Route path="planner" element={<RoutePlanner />} />
          <Route path="routes" element={<ActiveRoutes />} />
          <Route path="live" element={<LiveOps />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="optimization" element={<OptimizationHistory />} />
          <Route path="settings" element={<Settings />} />
        </Route>

        {/* Unknown paths redirect rather than showing a 404, and land on "/" —
            which then sends them to /login if they are not signed in.
            `replace` keeps the bad URL out of the back-button history. */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>

      {/* Outside <Routes> on purpose: a toast raised by an action that then
          navigates (accepting a plan, saving an order) must survive that
          navigation instead of unmounting with the page that raised it. */}
      <ToastHost />
    </>
  );
}
