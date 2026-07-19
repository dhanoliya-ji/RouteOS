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

function Protected({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="p-10 text-ink-500">Loading…</div>;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  const loadUser = useAuth((s) => s.loadUser);
  useEffect(() => {
    loadUser();
  }, [loadUser]);

  return (
    <>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/"
          element={
            <Protected>
              <Layout />
            </Protected>
          }
        >
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
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <ToastHost />
    </>
  );
}
