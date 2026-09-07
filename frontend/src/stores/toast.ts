// The notification queue. The second of two global stores.
//
// Qualifies as global because a toast is RAISED deep inside a mutation and
// RENDERED by <ToastHost/> at the app root — the two have no props path
// between them.
import { create } from "zustand";

export interface Toast {
  id: number;
  message: string;
  kind: "success" | "error" | "info";
}

interface ToastState {
  toasts: Toast[];
  push: (message: string, kind?: Toast["kind"]) => void;
  dismiss: (id: number) => void;
}

// Monotonic id source. Not Date.now(), which collides when two toasts are
// pushed in the same millisecond, and not Math.random(). A counter is
// collision-free for the life of a page load, which is all a React key needs.
let counter = 0;

export const useToast = create<ToastState>((set) => ({
  toasts: [],
  push: (message, kind = "info") => {
    const id = ++counter;
    set((s) => ({ toasts: [...s.toasts, { id, message, kind }] }));
    // Each toast removes itself after 4s.
    //
    // The updater is a FUNCTION of previous state, not a captured snapshot.
    // With a snapshot computed at push time, two overlapping toasts would each
    // restore the other's entry when their timers fired.
    //
    // The timer is not cancelled on unmount — harmless, since the store
    // outlives every component and the callback only filters an array.
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 4000);
  },
  // Manual removal. Currently has no caller: the toasts are not clickable, so
  // every one disappears on its own timer.
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));
