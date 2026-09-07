// Who is signed in. One of two global stores.
//
// This is client state we own, needed by the router guard, the sidebar and half
// the pages — too far apart to pass as props, which is what qualifies it for a
// store. Server data does NOT belong here; that is TanStack Query's job.
import { create } from "zustand";
import type { User } from "../types";
import { authApi } from "../api/endpoints";
import { tokenStore } from "../api/client";

interface AuthState {
  user: User | null;
  // Starts true, and that matters. On a page refresh the token is in
  // localStorage but the user object is not, and confirming the token takes a
  // round trip — so there is a moment that is neither signed in nor signed
  // out. App.tsx's <Protected> waits on this flag rather than treating that
  // moment as signed out and bouncing the user to /login on every reload.
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  loadUser: () => Promise<void>;
}

export const useAuth = create<AuthState>((set) => ({
  user: null,
  loading: true,
  login: async (email, password) => {
    const { access_token } = await authApi.login(email, password);
    // Store the token BEFORE calling /auth/me — that request needs it, since
    // client.ts reads the header from tokenStore.
    tokenStore.set(access_token);
    const user = await authApi.me();
    set({ user });
    // Errors are deliberately not caught: Login awaits this and renders the
    // failure itself, which is why there is no error field on this store.
  },
  logout: () => {
    // Local only — there is no server-side session to end, since a JWT stays
    // valid until it expires. Dropping the token is the whole logout.
    tokenStore.clear();
    set({ user: null });
  },
  loadUser: async () => {
    // No token at all: settled immediately as signed out, with no request.
    if (!tokenStore.get()) {
      set({ user: null, loading: false });
      return;
    }
    try {
      // The only way to know a stored token is still good — it may have
      // expired, or the account may have been disabled since it was issued.
      const user = await authApi.me();
      set({ user, loading: false });
    } catch {
      // Any failure is treated as "this token is no good" and it is discarded.
      // Right for the common cases (expired, revoked, disabled) and slightly
      // wrong for one: a transient network error also logs the user out.
      // Distinguishing them would mean inspecting the ApiError's status.
      tokenStore.clear();
      set({ user: null, loading: false });
    }
    // Note every branch sets loading: false. Missing it on any path would
    // leave <Protected> stuck on "Loading…" forever.
  },
}));

// Whether a role may write. Used to hide buttons a VIEWER cannot use.
//
// A COURTESY, NOT A CONTROL: the backend enforces permissions, and this only
// avoids showing a control that would return 403. Mirrors the backend's ladder,
// where require_roles short-circuits on ADMIN, so the two agree by construction.
export const canManage = (role?: string) => role === "ADMIN" || role === "DISPATCHER";
