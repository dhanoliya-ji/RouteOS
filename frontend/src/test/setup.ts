// Runs before every test file (wired up in vite.config.ts).
//
// Two jobs: add the DOM matchers, and reset the state that would otherwise leak
// from one test to the next. That leakage is the main hazard in a frontend
// suite — the modules here hold real state at module scope (the Zustand stores,
// the token in localStorage), so without this a test's result would depend on
// which tests ran before it.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

beforeEach(() => {
  // The token lives in localStorage and is read by every request. A test that
  // signs in would otherwise leave the next one authenticated.
  localStorage.clear();
});

afterEach(() => {
  // Unmount anything still rendered, so an effect from a previous test cannot
  // fire during the next one.
  cleanup();
  // Drop fetch/WebSocket stubs and restore any spied-on globals.
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  // Return to real timers in case a test opted into fake ones.
  vi.useRealTimers();
});
