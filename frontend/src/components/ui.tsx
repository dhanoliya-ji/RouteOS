// Shared presentational primitives.
//
// Props in, markup out. Only ToastHost holds any state, and only by
// subscribing to the toast store. Anything used by a single screen stays in
// that screen's file instead — which is why RoutePlanner keeps its own Metric
// and Settings its own Row.
import type { ReactNode } from "react";
import { useToast } from "../stores/toast";

// One lookup table covering FOUR different backend enums — order status,
// vehicle status, route status and priority. That works only because none of
// their values collide, and the payoff is that a given status is coloured
// identically wherever it appears in the app.
//
// The colours carry meaning consistently: amber = waiting, blue/indigo = in
// progress, green = done, red = failed, grey = inert.
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

/** A coloured pill for any status or priority value. */
export function StatusBadge({ value }: { value: string }) {
  return (
    // The `||` fallback means a value the backend adds later renders in neutral
    // grey rather than unstyled — it degrades instead of breaking.
    <span className={`badge ${STATUS_COLORS[value] || "bg-ink-100 text-ink-700"}`}>
      {/* Turns OUT_FOR_DELIVERY into "OUT FOR DELIVERY", so the wire format
          never reaches the user. */}
      {value.replaceAll("_", " ")}
    </span>
  );
}

/**
 * A single headline number, used across the Dashboard and Analytics.
 *
 * `value` is a ReactNode rather than a string so a caller can pass a formatted
 * fragment; `accent` lets one tile be coloured for emphasis without a variant
 * prop per colour.
 */
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

/** "There is nothing here" — an empty result, not a failure. */
export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    // Dashed border to read as a placeholder rather than as content.
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-ink-300 bg-white/50 py-12 text-center">
      <div className="text-sm font-medium text-ink-600">{title}</div>
      {hint && <div className="mt-1 text-xs text-ink-400">{hint}</div>}
    </div>
  );
}

/**
 * "The request failed" — distinct from EmptyState above.
 *
 * NOT CURRENTLY USED, and that is a real gap rather than a style choice: no
 * page checks its query's `isError`, so a failed read falls through to the
 * empty branch and reads as "no orders" when it should read "could not load
 * orders". Wiring it up is mechanical, per query:
 *
 *   {q.isLoading ? <Skeleton/> : q.isError ? <ErrorState message={...}/> : …}
 *
 * Mutations are unaffected — those already report failures through toasts.
 */
export function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
      {message}
    </div>
  );
}

/**
 * The loading placeholder, and the app's ONLY loading idiom — there is no
 * spinner. Consistency is the point: every screen loads the same way.
 *
 * Sized by the caller (`<Skeleton className="h-64" />`) so the placeholder can
 * match the shape of whatever it stands in for, which stops the layout jumping
 * when the data arrives.
 */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-ink-200 ${className}`} />;
}

/**
 * Renders the toast queue. Mounted once in App.tsx, outside <Routes> so a
 * toast survives the navigation that often follows the action that raised it.
 *
 * Each entry removes itself on a timer owned by the store, so there is nothing
 * to clean up here.
 */
export function ToastHost() {
  const { toasts } = useToast();
  return (
    // z-[1000] — above Modal's z-[900], so a toast raised by a form's own
    // mutation appears in front of that form rather than behind it.
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

/**
 * The dialog every create/edit form lives in — forms are modals here rather
 * than routes of their own.
 */
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
  // Returns null rather than hiding with CSS, so the children UNMOUNT when
  // closed. That is what resets a form's state between openings — otherwise the
  // order you edited last time would still be filled in when you next click
  // "New Order".
  if (!open) return null;
  return (
    // The backdrop closes the modal...
    <div className="fixed inset-0 z-[900] flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className={`card max-h-[90vh] w-full overflow-y-auto p-5 ${wide ? "max-w-3xl" : "max-w-lg"}`}
        // ...but the panel does not. Without this stopPropagation, a click
        // anywhere inside the form would bubble to the backdrop and dismiss it
        // mid-edit.
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
