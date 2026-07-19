import { useState } from "react";
import { MapContainer, Marker, Popup, TileLayer } from "react-leaflet";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "../components/Layout";
import { Modal, Skeleton } from "../components/ui";
import { depotApi, vehicleApi } from "../api/endpoints";
import { useToast } from "../stores/toast";
import { useAuth, canManage } from "../stores/auth";
import { NCR_CENTER, depotIcon } from "../utils/map";
import type { Depot } from "../types";

export default function Depots() {
  const qc = useQueryClient();
  const push = useToast((s) => s.push);
  const editable = canManage(useAuth((s) => s.user?.role));
  const [open, setOpen] = useState(false);

  const depots = useQuery({ queryKey: ["depots"], queryFn: depotApi.list });
  const vehicles = useQuery({ queryKey: ["vehicles"], queryFn: () => vehicleApi.list() });

  const [form, setForm] = useState({ name: "", address: "", latitude: 28.55, longitude: 77.25 });
  const set = (k: string, v: any) => setForm((f) => ({ ...f, [k]: v }));
  const mut = useMutation({
    mutationFn: () => depotApi.create(form as any),
    onSuccess: () => { push("Depot created", "success"); setOpen(false); qc.invalidateQueries({ queryKey: ["depots"] }); },
    onError: (e: any) => push(e.message, "error"),
  });

  return (
    <div>
      <PageHeader title="Depots" subtitle="Warehouses & dispatch hubs" actions={editable && <button className="btn-primary" onClick={() => setOpen(true)}>+ Add Depot</button>} />
      <div className="grid grid-cols-1 gap-4 p-6 lg:grid-cols-2">
        <div className="card overflow-hidden" style={{ height: 420 }}>
          <MapContainer center={NCR_CENTER} zoom={10} className="h-full">
            <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="© OpenStreetMap" />
            {(depots.data || []).map((d) => (
              <Marker key={d.id} position={[d.latitude, d.longitude]} icon={depotIcon}>
                <Popup>{d.name}</Popup>
              </Marker>
            ))}
          </MapContainer>
        </div>
        <div className="space-y-3">
          {depots.isLoading ? <Skeleton className="h-40" /> : (depots.data || []).map((d: Depot) => {
            const count = (vehicles.data || []).filter((v) => v.home_depot_id === d.id).length;
            return (
              <div key={d.id} className="card p-4">
                <div className="font-semibold">{d.name}</div>
                <div className="text-sm text-ink-500">{d.address}</div>
                <div className="mt-2 flex gap-4 text-xs text-ink-400">
                  <span>{d.latitude.toFixed(4)}, {d.longitude.toFixed(4)}</span>
                  <span>{count} vehicles</span>
                  <span>{d.operating_start}–{d.operating_end}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
      <Modal open={open} onClose={() => setOpen(false)} title="New Depot">
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2"><label className="label">Name</label><input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} /></div>
          <div className="col-span-2"><label className="label">Address</label><input className="input" value={form.address} onChange={(e) => set("address", e.target.value)} /></div>
          <div><label className="label">Latitude</label><input className="input" type="number" step="0.0001" value={form.latitude} onChange={(e) => set("latitude", Number(e.target.value))} /></div>
          <div><label className="label">Longitude</label><input className="input" type="number" step="0.0001" value={form.longitude} onChange={(e) => set("longitude", Number(e.target.value))} /></div>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button className="btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
          <button className="btn-primary" disabled={mut.isPending} onClick={() => mut.mutate()}>Save</button>
        </div>
      </Modal>
    </div>
  );
}
