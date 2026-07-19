import type { ReactNode } from "react";
import { useToast } from "../stores/toast";

const STATUS_COLORS: Record<string, string> = {
  PENDING: "bg-amber-100 text-amber-800",
  ASSIGNED: "bg-blue-100 text-blue-800",
  OUT_FOR_DELIVERY: "bg-indigo-100 text-indigo-800",
  DELIVERED: "bg-green-100 text-green-800",
  FAILED: "bg-red-100 text-red-800",
  CANCELLED: "bg-ink-200 text-ink-700",
  AVAILABLE: "bg-green-100 text-green-800",
  IN_TRANSIT: "bg-indigo-100 text-indigo-800",
  MAINTENANCE: "bg-amber-100 text-amber-800",
  OFFLINE: "bg-ink-200 text-ink-700",
  PLANNED: "bg-blue-100 text-blue-800",
  ACTIVE: "bg-indigo-100 text-indigo-800",
  COMPLETED: "bg-green-100 text-green-800",
  LOW: "bg-ink-100 text-ink-600",
  NORMAL: "bg-sky-100 text-sky-700",
  HIGH: "bg-orange-100 text-orange-800",
  URGENT: "bg-red-100 text-red-800",
};

export function StatusBadge({ value }: { value: string }) {
  return (
    <span className={`badge ${STATUS_COLORS[value] || "bg-ink-100 text-ink-700"}`}>
      {value.replaceAll("_", " ")}
    </span>
  );
}

export function KpiCard({
  label,
  value,
  hint,
  accent,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  accent?: string;
}) {
  return (
    <div className="card p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-ink-400">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${accent || "text-ink-900"}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-ink-400">{hint}</div>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink-300 border-t-brand-600" />
      {label}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-ink-300 bg-white/50 py-12 text-center">
      <div className="text-sm font-medium text-ink-600">{title}</div>
      {hint && <div className="mt-1 text-xs text-ink-400">{hint}</div>}
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
      {message}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-ink-200 ${className}`} />;
}

export function ToastHost() {
  const { toasts } = useToast();
  return (
    <div className="fixed bottom-4 right-4 z-[1000] flex flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`rounded-lg px-4 py-2.5 text-sm text-white shadow-lg ${
            t.kind === "success" ? "bg-green-600" : t.kind === "error" ? "bg-red-600" : "bg-ink-800"
          }`}
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  wide?: boolean;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[900] flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className={`card max-h-[90vh] w-full overflow-y-auto p-5 ${wide ? "max-w-3xl" : "max-w-lg"}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold">{title}</h3>
          <button className="text-ink-400 hover:text-ink-700" onClick={onClose}>
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
