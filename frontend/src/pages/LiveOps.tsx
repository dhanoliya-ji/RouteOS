import { useMemo, useRef, useState } from "react";
import { MapContainer, Marker, Polyline, Popup, TileLayer } from "react-leaflet";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "../components/Layout";
import { StatusBadge } from "../components/ui";
import { depotApi, routeApi, simulationApi } from "../api/endpoints";
import { useFleetSocket } from "../hooks/useFleetSocket";
import { useToast } from "../stores/toast";
import { NCR_CENTER, ROUTE_COLORS, coloredDot, depotIcon, vehicleIcon } from "../utils/map";
import type { WsEvent } from "../types";

interface LiveVehicle {
  vehicle_id: number;
  route_id: number;
  lat: number;
  lon: number;
}

export default function LiveOps() {
  const qc = useQueryClient();
  const push = useToast((s) => s.push);
  const [speed, setSpeed] = useState(20);
  const [positions, setPositions] = useState<Record<number, LiveVehicle>>({});
  const [feed, setFeed] = useState<string[]>([]);
  const [selectedRoute, setSelectedRoute] = useState<number | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const feedRef = useRef<string[]>([]);

  const depots = useQuery({ queryKey: ["depots"], queryFn: depotApi.list });
  const depot = depots.data?.[0];
  const routes = useQuery({ queryKey: ["active-routes"], queryFn: () => routeApi.list("ACTIVE"), refetchInterval: 5000 });
  const simStatus = useQuery({ queryKey: ["sim-status"], queryFn: simulationApi.status, refetchInterval: 5000 });

  const pushFeed = (msg: string) => {
    feedRef.current = [`${new Date().toLocaleTimeString()} · ${msg}`, ...feedRef.current].slice(0, 40);
    setFeed([...feedRef.current]);
  };

  const onEvent = (e: WsEvent) => {
    switch (e.type) {
      case "SNAPSHOT":
        if (e.data?.vehicles) {
          const next: Record<number, LiveVehicle> = {};
          for (const v of e.data.vehicles) {
            next[v.vehicle_id] = { vehicle_id: v.vehicle_id, route_id: v.route_id, lat: v.latitude, lon: v.longitude };
          }
          setPositions(next);
        }
        break;
      case "VEHICLE_LOCATION_UPDATED":
        setPositions((p) => ({
          ...p,
          [e.data.vehicle_id]: {
            vehicle_id: e.data.vehicle_id,
            route_id: e.data.route_id,
            lat: e.data.latitude,
            lon: e.data.longitude,
          },
        }));
        break;
      case "DELIVERY_COMPLETED":
        pushFeed(`Delivered order #${e.data.order_id} (stop ${e.data.stop_sequence})`);
        break;
      case "ROUTE_STATUS_UPDATED":
        pushFeed(`Route ${e.data.route_code} → ${e.data.status}`);
        qc.invalidateQueries({ queryKey: ["active-routes"] });
        break;
      case "ROUTE_DELAYED":
        setWarning(
          `Route ${e.data.route_id}: +${e.data.added_delay_minutes} min delay (${e.data.severity})` +
            (e.data.late_orders?.length ? ` — ${e.data.late_orders.length} deliveries at risk` : "")
        );
        pushFeed(`Traffic on route ${e.data.route_id}: +${e.data.added_delay_minutes} min`);
        break;
      case "ROUTE_REOPTIMIZED":
        pushFeed(`Route ${e.data.route_id} re-optimized (${e.data.remaining_distance_km} km remaining)`);
        setWarning(null);
        break;
      case "SIMULATION_STOPPED":
        pushFeed("Simulation stopped");
        break;
    }
  };

  const { connected } = useFleetSocket(onEvent);

  const startMut = useMutation({
    mutationFn: () => simulationApi.start(speed),
    onSuccess: (r) => {
      if (r.started === false) push(r.reason || "Nothing to simulate", "error");
      else push(`Simulation started at ${speed}x`, "success");
    },
  });
  const stopMut = useMutation({ mutationFn: () => simulationApi.stop(), onSuccess: () => push("Stopped", "info") });
  const trafficMut = useMutation({
    mutationFn: (sev: string) => simulationApi.traffic(selectedRoute!, sev),
    onSuccess: (r) => push(`Traffic applied: +${r.added_delay_minutes} min`, "info"),
    onError: (e: any) => push(e.message, "error"),
  });
  const reoptMut = useMutation({
    mutationFn: () => simulationApi.reoptimize(selectedRoute!),
    onSuccess: (r) => {
      push(r.reoptimized ? "Route re-optimized" : r.reason, r.reoptimized ? "success" : "info");
      qc.invalidateQueries({ queryKey: ["active-routes"] });
    },
    onError: (e: any) => push(e.message, "error"),
  });

  const routePolylines = useMemo(() => {
    if (!depot) return [];
    return (routes.data || []).map((r, i) => ({
      id: r.id,
      color: ROUTE_COLORS[i % ROUTE_COLORS.length],
      positions: [
        [depot.latitude, depot.longitude],
        ...[...r.stops].sort((a, b) => a.stop_sequence - b.stop_sequence).map((s) => [s.latitude, s.longitude] as [number, number]),
        [depot.latitude, depot.longitude],
      ] as [number, number][],
    }));
  }, [routes.data, depot]);

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Live Operations"
        subtitle={
          <span className="flex items-center gap-2">
            <span className={`inline-block h-2 w-2 rounded-full ${connected ? "bg-green-500" : "bg-red-500"}`} />
            {connected ? "WebSocket connected" : "reconnecting…"}
          </span> as any
        }
        actions={
          <div className="flex items-center gap-2">
            <select className="input py-1.5" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
              <option value={1}>1x</option>
              <option value={5}>5x</option>
              <option value={20}>20x</option>
            </select>
            <button className="btn-primary" disabled={startMut.isPending} onClick={() => startMut.mutate()}>▶ Start Simulation</button>
            <button className="btn-ghost" onClick={() => stopMut.mutate()}>■ Stop</button>
          </div>
        }
      />
      {warning && (
        <div className="flex items-center justify-between bg-amber-50 px-6 py-2 text-sm text-amber-800">
          <span>⚠ {warning}</span>
          <button className="btn-primary py-1" disabled={!selectedRoute || reoptMut.isPending} onClick={() => reoptMut.mutate()}>
            Reoptimize affected route
          </button>
        </div>
      )}
      <div className="grid flex-1 grid-cols-12 overflow-hidden">
        <div className="col-span-9">
          <MapContainer center={NCR_CENTER} zoom={11} className="h-full">
            <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="© OpenStreetMap" />
            {depot && <Marker position={[depot.latitude, depot.longitude]} icon={depotIcon}><Popup>{depot.name}</Popup></Marker>}
            {routePolylines.map((p, i) => (
              <Polyline
                key={p.id}
                positions={p.positions}
                pathOptions={{ color: p.color, weight: selectedRoute === p.id ? 6 : 3, opacity: selectedRoute === p.id ? 0.95 : 0.55 }}
                eventHandlers={{ click: () => setSelectedRoute(p.id) }}
              />
            ))}
            {Object.values(positions).map((v) => {
              const idx = routePolylines.findIndex((p) => p.id === v.route_id);
              return (
                <Marker key={v.vehicle_id} position={[v.lat, v.lon]} icon={vehicleIcon(ROUTE_COLORS[(idx < 0 ? 0 : idx) % ROUTE_COLORS.length])}>
                  <Popup>Vehicle #{v.vehicle_id} · route {v.route_id}</Popup>
                </Marker>
              );
            })}
          </MapContainer>
        </div>
        <div className="col-span-3 flex flex-col overflow-hidden border-l border-ink-200 bg-white">
          <div className="border-b border-ink-200 p-4">
            <h3 className="mb-2 text-sm font-semibold">Active Routes ({routes.data?.length || 0})</h3>
            <div className="max-h-56 space-y-1 overflow-y-auto">
              {(routes.data || []).map((r) => (
                <button
                  key={r.id}
                  onClick={() => setSelectedRoute(r.id)}
                  className={`flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-sm ${selectedRoute === r.id ? "bg-brand-50 text-brand-700" : "hover:bg-ink-50"}`}
                >
                  <span>{r.route_code}</span>
                  <span className="text-xs text-ink-400">{r.progress_stop_index + 1}/{r.stops.length}</span>
                  <StatusBadge value={r.status} />
                </button>
              ))}
            </div>
            {selectedRoute && (
              <div className="mt-3 space-y-2">
                <div className="text-xs font-medium text-ink-500">Disrupt route {selectedRoute}</div>
                <div className="flex flex-wrap gap-1">
                  {["moderate", "severe", "breakdown"].map((sev) => (
                    <button key={sev} className="btn-ghost px-2 py-1 text-xs" onClick={() => trafficMut.mutate(sev)}>
                      {sev}
                    </button>
                  ))}
                  <button className="btn-ghost px-2 py-1 text-xs" onClick={() => trafficMut.mutate("clear")}>clear</button>
                </div>
                <button className="btn-primary w-full py-1.5 text-sm" disabled={reoptMut.isPending} onClick={() => reoptMut.mutate()}>
                  Reoptimize remaining stops
                </button>
              </div>
            )}
          </div>
          <div className="flex-1 overflow-y-auto p-4">
            <h3 className="mb-2 text-sm font-semibold">Event Feed</h3>
            <div className="space-y-1 text-xs text-ink-600">
              {feed.length === 0 ? <div className="text-ink-400">No events yet.</div> : feed.map((f, i) => <div key={i}>{f}</div>)}
            </div>
          </div>
          <div className="border-t border-ink-200 px-4 py-2 text-xs text-ink-400">
            {simStatus.data?.running ? `Simulating ${simStatus.data.active_vehicles} vehicles @ ${simStatus.data.speed_multiplier}x` : "Idle"}
          </div>
        </div>
      </div>
    </div>
  );
}
