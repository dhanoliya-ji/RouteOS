import { create } from "zustand";
import type { User } from "../types";
import { authApi } from "../api/endpoints";
import { tokenStore } from "../api/client";

interface AuthState {
  user: User | null;
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
    tokenStore.set(access_token);
    const user = await authApi.me();
    set({ user });
  },
  logout: () => {
    tokenStore.clear();
    set({ user: null });
  },
  loadUser: async () => {
    if (!tokenStore.get()) {
      set({ user: null, loading: false });
      return;
    }
    try {
      const user = await authApi.me();
      set({ user, loading: false });
    } catch {
      tokenStore.clear();
      set({ user: null, loading: false });
    }
  },
}));

export const canManage = (role?: string) => role === "ADMIN" || role === "DISPATCHER";
