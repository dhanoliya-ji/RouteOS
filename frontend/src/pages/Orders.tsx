import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "../components/Layout";
import { EmptyState, Modal, Skeleton, StatusBadge } from "../components/ui";
import { depotApi, orderApi } from "../api/endpoints";
import { useToast } from "../stores/toast";
import { useAuth, canManage } from "../stores/auth";
import type { Order } from "../types";

const STATUSES = ["PENDING", "ASSIGNED", "OUT_FOR_DELIVERY", "DELIVERED", "FAILED", "CANCELLED"];
const PRIORITIES = ["LOW", "NORMAL", "HIGH", "URGENT"];

export default function Orders() {
  const qc = useQueryClient();
  const push = useToast((s) => s.push);
  const role = useAuth((s) => s.user?.role);
  const editable = canManage(role);

  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [priority, setPriority] = useState("");
  const [search, setSearch] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Order | null>(null);

  const depots = useQuery({ queryKey: ["depots"], queryFn: depotApi.list });
  const orders = useQuery({
    queryKey: ["orders", page, status, priority, search],
    queryFn: () => orderApi.list({ page, page_size: 20, status, priority, search }),
  });

  const cancelMut = useMutation({
    mutationFn: (id: number) => orderApi.cancel(id),
    onSuccess: () => {
      push("Order cancelled", "success");
      qc.invalidateQueries({ queryKey: ["orders"] });
    },
    onError: (e: any) => push(e.message, "error"),
  });

  const openCreate = () => {
    setEditing(null);
    setModalOpen(true);
  };
  const openEdit = (o: Order) => {
    setEditing(o);
    setModalOpen(true);
  };

  return (
    <div>
      <PageHeader
        title="Orders"
        subtitle="Delivery order management"
        actions={editable && <button className="btn-primary" onClick={openCreate}>+ New Order</button>}
      />
      <div className="space-y-4 p-6">
        <div className="flex flex-wrap gap-2">
          <input
            className="input max-w-xs"
            placeholder="Search order / customer / address"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
          />
          <select className="input max-w-[160px]" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            <option value="">All statuses</option>
            {STATUSES.map((s) => <option key={s}>{s}</option>)}
          </select>
          <select className="input max-w-[160px]" value={priority} onChange={(e) => { setPriority(e.target.value); setPage(1); }}>
            <option value="">All priorities</option>
            {PRIORITIES.map((p) => <option key={p}>{p}</option>)}
          </select>
        </div>

        <div className="card overflow-hidden">
          {orders.isLoading ? (
            <div className="p-4"><Skeleton className="h-64" /></div>
          ) : !orders.data || orders.data.items.length === 0 ? (
            <div className="p-6"><EmptyState title="No orders found" /></div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-ink-50 text-left text-xs uppercase text-ink-400">
                  <tr>
                    <th className="px-4 py-2.5">Order</th>
                    <th className="px-4 py-2.5">Customer</th>
                    <th className="px-4 py-2.5">Address</th>
                    <th className="px-4 py-2.5">Weight</th>
                    <th className="px-4 py-2.5">Priority</th>
                    <th className="px-4 py-2.5">Status</th>
                    <th className="px-4 py-2.5"></th>
                  </tr>
                </thead>
                <tbody>
                  {orders.data.items.map((o) => (
                    <tr key={o.id} className="border-t border-ink-100 hover:bg-ink-50">
                      <td className="px-4 py-2.5 font-medium">{o.order_number}</td>
                      <td className="px-4 py-2.5">{o.customer_name}</td>
                      <td className="px-4 py-2.5 max-w-[220px] truncate text-ink-500">{o.delivery_address}</td>
                      <td className="px-4 py-2.5">{o.weight_kg} kg</td>
                      <td className="px-4 py-2.5"><StatusBadge value={o.priority} /></td>
                      <td className="px-4 py-2.5"><StatusBadge value={o.status} /></td>
                      <td className="px-4 py-2.5 text-right">
                        {editable && (
                          <div className="flex justify-end gap-2">
                            <button className="text-brand-600 hover:underline" onClick={() => openEdit(o)}>Edit</button>
                            {o.status === "PENDING" && (
                              <button className="text-red-600 hover:underline" onClick={() => cancelMut.mutate(o.id)}>
                                Cancel
                              </button>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {orders.data && (
          <div className="flex items-center justify-between text-sm text-ink-500">
            <span>{orders.data.total} orders</span>
            <div className="flex items-center gap-2">
              <button className="btn-ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</button>
              <span>Page {orders.data.page} / {Math.max(1, orders.data.pages)}</span>
              <button className="btn-ghost" disabled={page >= orders.data.pages} onClick={() => setPage((p) => p + 1)}>Next</button>
            </div>
          </div>
        )}
      </div>

      {modalOpen && (
        <OrderForm
          order={editing}
          depots={depots.data || []}
          onClose={() => setModalOpen(false)}
          onSaved={() => {
            setModalOpen(false);
            qc.invalidateQueries({ queryKey: ["orders"] });
          }}
        />
      )}
    </div>
  );
}

function OrderForm({
  order,
  depots,
  onClose,
  onSaved,
}: {
  order: Order | null;
  depots: { id: number; name: string }[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const push = useToast((s) => s.push);
  const [form, setForm] = useState({
    customer_name: order?.customer_name || "",
    customer_phone: order?.customer_phone || "",
    delivery_address: order?.delivery_address || "",
    latitude: order?.latitude ?? 28.55,
    longitude: order?.longitude ?? 77.25,
    weight_kg: order?.weight_kg ?? 5,
    priority: order?.priority || "NORMAL",
    service_time_minutes: order?.service_time_minutes ?? 10,
    depot_id: order?.depot_id ?? depots[0]?.id ?? 1,
  });

  const mut = useMutation({
    mutationFn: () =>
      order ? orderApi.update(order.id, form as any) : orderApi.create(form as any),
    onSuccess: () => {
      push(order ? "Order updated" : "Order created", "success");
      onSaved();
    },
    onError: (e: any) => push(e.message, "error"),
  });

  const set = (k: string, v: any) => setForm((f) => ({ ...f, [k]: v }));

  return (
    <Modal open onClose={onClose} title={order ? `Edit ${order.order_number}` : "New Order"}>
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2">
          <label className="label">Customer name</label>
          <input className="input" value={form.customer_name} onChange={(e) => set("customer_name", e.target.value)} />
        </div>
        <div>
          <label className="label">Phone</label>
          <input className="input" value={form.customer_phone} onChange={(e) => set("customer_phone", e.target.value)} />
        </div>
        <div>
          <label className="label">Depot</label>
          <select className="input" value={form.depot_id} onChange={(e) => set("depot_id", Number(e.target.value))}>
            {depots.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </div>
        <div className="col-span-2">
          <label className="label">Delivery address</label>
          <input className="input" value={form.delivery_address} onChange={(e) => set("delivery_address", e.target.value)} />
        </div>
        <div>
          <label className="label">Latitude</label>
          <input className="input" type="number" step="0.0001" value={form.latitude} onChange={(e) => set("latitude", Number(e.target.value))} />
        </div>
        <div>
          <label className="label">Longitude</label>
          <input className="input" type="number" step="0.0001" value={form.longitude} onChange={(e) => set("longitude", Number(e.target.value))} />
        </div>
        <div>
          <label className="label">Weight (kg)</label>
          <input className="input" type="number" step="0.1" value={form.weight_kg} onChange={(e) => set("weight_kg", Number(e.target.value))} />
        </div>
        <div>
          <label className="label">Priority</label>
          <select className="input" value={form.priority} onChange={(e) => set("priority", e.target.value)}>
            {PRIORITIES.map((p) => <option key={p}>{p}</option>)}
          </select>
        </div>
        <div>
          <label className="label">Service time (min)</label>
          <input className="input" type="number" value={form.service_time_minutes} onChange={(e) => set("service_time_minutes", Number(e.target.value))} />
        </div>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? "Saving…" : "Save"}
        </button>
      </div>
    </Modal>
  );
}
