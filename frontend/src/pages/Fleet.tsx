import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "../components/Layout";
import { EmptyState, Modal, Skeleton, StatusBadge } from "../components/ui";
import { depotApi, vehicleApi } from "../api/endpoints";
import { useToast } from "../stores/toast";
import { useAuth, canManage } from "../stores/auth";
import type { Vehicle } from "../types";

const STATUSES = ["AVAILABLE", "ASSIGNED", "IN_TRANSIT", "MAINTENANCE", "OFFLINE"];

export default function Fleet() {
  const qc = useQueryClient();
  const push = useToast((s) => s.push);
  const editable = canManage(useAuth((s) => s.user?.role));
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Vehicle | null>(null);

  const vehicles = useQuery({ queryKey: ["vehicles"], queryFn: () => vehicleApi.list(), refetchInterval: 6000 });
  const depots = useQuery({ queryKey: ["depots"], queryFn: depotApi.list });

  const statusMut = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => vehicleApi.update(id, { status } as any),
    onSuccess: () => {
      push("Vehicle updated", "success");
      qc.invalidateQueries({ queryKey: ["vehicles"] });
    },
    onError: (e: any) => push(e.message, "error"),
  });

  return (
    <div>
      <PageHeader
        title="Fleet"
        subtitle="Vehicles & drivers"
        actions={editable && <button className="btn-primary" onClick={() => { setEditing(null); setModalOpen(true); }}>+ Add Vehicle</button>}
      />
      <div className="p-6">
        {vehicles.isLoading ? (
          <Skeleton className="h-64" />
        ) : !vehicles.data || vehicles.data.length === 0 ? (
          <EmptyState title="No vehicles" />
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {vehicles.data.map((v) => {
              const util = v.capacity_kg ? Math.round((v.current_load_kg / v.capacity_kg) * 100) : 0;
              return (
                <div key={v.id} className="card p-4">
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="font-semibold">{v.registration_number}</div>
                      <div className="text-sm text-ink-500">{v.driver_name} · {v.vehicle_type}</div>
                    </div>
                    <StatusBadge value={v.status} />
                  </div>
                  <div className="mt-3 text-xs text-ink-500">
                    Load {v.current_load_kg} / {v.capacity_kg} kg
                    <div className="mt-1 h-1.5 w-full rounded-full bg-ink-100">
                      <div className="h-1.5 rounded-full bg-brand-500" style={{ width: `${Math.min(util, 100)}%` }} />
                    </div>
                  </div>
                  {editable && (
                    <div className="mt-3 flex items-center gap-2">
                      <select
                        className="input py-1 text-xs"
                        value={v.status}
                        onChange={(e) => statusMut.mutate({ id: v.id, status: e.target.value })}
                      >
                        {STATUSES.map((s) => <option key={s}>{s}</option>)}
                      </select>
                      <button className="text-sm text-brand-600 hover:underline" onClick={() => { setEditing(v); setModalOpen(true); }}>
                        Edit
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
      {modalOpen && (
        <VehicleForm
          vehicle={editing}
          depots={depots.data || []}
          onClose={() => setModalOpen(false)}
          onSaved={() => { setModalOpen(false); qc.invalidateQueries({ queryKey: ["vehicles"] }); }}
        />
      )}
    </div>
  );
}

function VehicleForm({ vehicle, depots, onClose, onSaved }: {
  vehicle: Vehicle | null;
  depots: { id: number; name: string }[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const push = useToast((s) => s.push);
  const [form, setForm] = useState({
    registration_number: vehicle?.registration_number || "",
    driver_name: vehicle?.driver_name || "",
    vehicle_type: vehicle?.vehicle_type || "VAN",
    capacity_kg: vehicle?.capacity_kg ?? 600,
    home_depot_id: vehicle?.home_depot_id ?? depots[0]?.id ?? 1,
    max_route_distance_km: vehicle?.max_route_distance_km ?? 200,
  });
  const set = (k: string, v: any) => setForm((f) => ({ ...f, [k]: v }));
  const mut = useMutation({
    mutationFn: () => (vehicle ? vehicleApi.update(vehicle.id, form as any) : vehicleApi.create(form as any)),
    onSuccess: () => { push(vehicle ? "Vehicle updated" : "Vehicle added", "success"); onSaved(); },
    onError: (e: any) => push(e.message, "error"),
  });
  return (
    <Modal open onClose={onClose} title={vehicle ? "Edit Vehicle" : "Add Vehicle"}>
      <div className="grid grid-cols-2 gap-3">
        <div><label className="label">Registration</label><input className="input" value={form.registration_number} onChange={(e) => set("registration_number", e.target.value)} /></div>
        <div><label className="label">Driver</label><input className="input" value={form.driver_name} onChange={(e) => set("driver_name", e.target.value)} /></div>
        <div><label className="label">Type</label><input className="input" value={form.vehicle_type} onChange={(e) => set("vehicle_type", e.target.value)} /></div>
        <div><label className="label">Capacity (kg)</label><input className="input" type="number" value={form.capacity_kg} onChange={(e) => set("capacity_kg", Number(e.target.value))} /></div>
        <div><label className="label">Depot</label><select className="input" value={form.home_depot_id} onChange={(e) => set("home_depot_id", Number(e.target.value))}>{depots.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></div>
        <div><label className="label">Max route (km)</label><input className="input" type="number" value={form.max_route_distance_km} onChange={(e) => set("max_route_distance_km", Number(e.target.value))} /></div>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={mut.isPending} onClick={() => mut.mutate()}>{mut.isPending ? "Saving…" : "Save"}</button>
      </div>
    </Modal>
  );
}
