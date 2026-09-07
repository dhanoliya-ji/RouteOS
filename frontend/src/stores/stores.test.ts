/**
 * Tests for the two global stores.
 *
 * Small files, but `auth` holds the state machine a page refresh depends on:
 * get `loading` wrong and every signed-in user is bounced to the login screen
 * on reload. That is invisible in development, where you rarely hard-refresh.
 *
 * Both stores are plain Zustand, so they are tested by calling their actions —
 * no component, no renderer.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { tokenStore } from "../api/client";
import { canManage, useAuth } from "./auth";
import { useToast } from "./toast";

/** Reset a store to a known state — they are module singletons. */
function resetAuth() {
  useAuth.setState({ user: null, loading: true });
}

const A_USER = {
  id: 1,
  name: "Dev Dispatcher",
  email: "dispatcher@routeos.dev",
  role: "DISPATCHER" as const,
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
};

/** Stub the endpoints module, so no request is made. */
vi.mock("../api/endpoints", () => ({
  authApi: {
    login: vi.fn(),
    me: vi.fn(),
    register: vi.fn(),
  },
}));

// Imported after the mock so the stubbed version is what the store holds.
const { authApi } = await import("../api/endpoints");

describe("auth: loading is a third state, not a flag", () => {
  beforeEach(resetAuth);

  it("starts in loading, neither signed in nor signed out", () => {
    /**
     * The property App.tsx's <Protected> depends on. If the store started at
     * loading:false with user:null, that gate would read "signed out" during
     * the round trip to /auth/me and redirect a user who is in fact signed in.
     */
    const s = useAuth.getState();
    expect(s.loading).toBe(true);
    expect(s.user).toBeNull();
  });

  it("settles to signed out immediately when there is no token", async () => {
    // No request should be made at all — there is nothing to validate.
    await useAuth.getState().loadUser();
    expect(useAuth.getState().loading).toBe(false);
    expect(useAuth.getState().user).toBeNull();
    expect(authApi.me).not.toHaveBeenCalled();
  });

  it("settles to signed in when a stored token is still good", async () => {
    tokenStore.set("a-good-token");
    vi.mocked(authApi.me).mockResolvedValueOnce(A_USER);

    await useAuth.getState().loadUser();

    expect(useAuth.getState().user).toEqual(A_USER);
    expect(useAuth.getState().loading).toBe(false);
  });

  it("discards a token the server rejects", async () => {
    // Expired, revoked, or the account was disabled — all indistinguishable
    // from here, and all mean the same thing: this token is no good.
    tokenStore.set("a-stale-token");
    vi.mocked(authApi.me).mockRejectedValueOnce(new Error("401"));

    await useAuth.getState().loadUser();

    expect(useAuth.getState().user).toBeNull();
    expect(tokenStore.get()).toBeNull();
    expect(useAuth.getState().loading).toBe(false);
  });

  it("leaves loading false on every path", async () => {
    /**
     * The failure this guards is a hang, not a wrong answer: miss the flag on
     * any branch and <Protected> shows "Loading…" forever, with no error.
     */
    for (const arrange of [
      () => {},                                                    // no token
      () => { tokenStore.set("t"); vi.mocked(authApi.me).mockResolvedValueOnce(A_USER); },
      () => { tokenStore.set("t"); vi.mocked(authApi.me).mockRejectedValueOnce(new Error("x")); },
    ]) {
      resetAuth();
      localStorage.clear();
      arrange();
      await useAuth.getState().loadUser();
      expect(useAuth.getState().loading).toBe(false);
    }
  });

  it("treats a network failure as signed out", async () => {
    /**
     * Documents a rough edge rather than asserting a wish. loadUser cannot
     * tell a rejected request apart from a rejected token, so a transient
     * network error logs the user out. Distinguishing them would mean
     * inspecting the ApiError's status.
     */
    tokenStore.set("perfectly-valid-token");
    vi.mocked(authApi.me).mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await useAuth.getState().loadUser();

    expect(useAuth.getState().user).toBeNull();
    expect(tokenStore.get()).toBeNull();
  });
});

describe("auth: login and logout", () => {
  beforeEach(resetAuth);

  it("stores the token before fetching the user", async () => {
    /**
     * Order matters: /auth/me needs the header, and client.ts reads it from
     * tokenStore. Fetching first would send an unauthenticated request.
     */
    const seenDuringMe: (string | null)[] = [];
    vi.mocked(authApi.login).mockResolvedValueOnce({ access_token: "fresh-token" });
    vi.mocked(authApi.me).mockImplementationOnce(async () => {
      seenDuringMe.push(tokenStore.get());
      return A_USER;
    });

    await useAuth.getState().login("dispatcher@routeos.dev", "dispatch12345");

    expect(seenDuringMe).toEqual(["fresh-token"]);
    expect(useAuth.getState().user).toEqual(A_USER);
  });

  it("lets a failed login reject, so the form can show the reason", async () => {
    // Deliberately not caught in the store — Login awaits this and renders the
    // message itself, which is why there is no error field on the store.
    vi.mocked(authApi.login).mockRejectedValueOnce(new Error("Incorrect email or password"));

    await expect(
      useAuth.getState().login("wrong@routeos.dev", "nope"),
    ).rejects.toThrow("Incorrect email or password");

    expect(useAuth.getState().user).toBeNull();
  });

  it("logout clears both the user and the token", () => {
    tokenStore.set("t");
    useAuth.setState({ user: A_USER, loading: false });

    useAuth.getState().logout();

    expect(useAuth.getState().user).toBeNull();
    expect(tokenStore.get()).toBeNull();
    // Local only — a JWT has no server-side session to end, so dropping the
    // token IS the logout.
  });
});

describe("canManage mirrors the backend ladder", () => {
  it("admits ADMIN and DISPATCHER, refuses VIEWER", () => {
    expect(canManage("ADMIN")).toBe(true);
    expect(canManage("DISPATCHER")).toBe(true);
    expect(canManage("VIEWER")).toBe(false);
  });

  it("refuses an absent or unknown role", () => {
    // Reached while the auth store is still loading, so it must not throw or
    // default to permissive.
    expect(canManage(undefined)).toBe(false);
    expect(canManage("SOMETHING_NEW")).toBe(false);
  });
});

describe("toast", () => {
  beforeEach(() => useToast.setState({ toasts: [] }));

  it("appends with a message and kind", () => {
    useToast.getState().push("Order created", "success");
    const [t] = useToast.getState().toasts;
    expect(t.message).toBe("Order created");
    expect(t.kind).toBe("success");
  });

  it("defaults to info", () => {
    useToast.getState().push("Just so you know");
    expect(useToast.getState().toasts[0].kind).toBe("info");
  });

  it("gives every toast a unique id, even within one millisecond", () => {
    /**
     * Why the store uses a counter rather than Date.now(): several toasts can
     * be pushed in the same tick, and duplicate React keys would make one
     * overwrite another in the rendered list.
     */
    for (let i = 0; i < 50; i++) useToast.getState().push(`m${i}`);
    const ids = useToast.getState().toasts.map((t) => t.id);
    expect(new Set(ids).size).toBe(50);
  });

  it("dismisses one toast without disturbing the others", () => {
    useToast.getState().push("first");
    useToast.getState().push("second");
    const [first] = useToast.getState().toasts;

    useToast.getState().dismiss(first.id);

    const left = useToast.getState().toasts;
    expect(left).toHaveLength(1);
    expect(left[0].message).toBe("second");
  });

  it("removes each toast on its own timer after 4 seconds", () => {
    vi.useFakeTimers();
    useToast.getState().push("temporary");
    expect(useToast.getState().toasts).toHaveLength(1);

    vi.advanceTimersByTime(3999);
    expect(useToast.getState().toasts).toHaveLength(1);

    vi.advanceTimersByTime(1);
    expect(useToast.getState().toasts).toHaveLength(0);
  });

  it("overlapping toasts do not erase each other", () => {
    /**
     * The reason the timer uses a function updater rather than a captured
     * snapshot. With a snapshot taken at push time, the first toast's timer
     * would restore a list that predates the second toast — so dismissing one
     * would resurrect or delete the other.
     */
    vi.useFakeTimers();
    useToast.getState().push("first");
    vi.advanceTimersByTime(2000);
    useToast.getState().push("second");

    // First expires; second has 2s left.
    vi.advanceTimersByTime(2000);
    let left = useToast.getState().toasts;
    expect(left).toHaveLength(1);
    expect(left[0].message).toBe("second");

    // Then the second goes too, and nothing comes back.
    vi.advanceTimersByTime(2000);
    expect(useToast.getState().toasts).toHaveLength(0);
  });
});
