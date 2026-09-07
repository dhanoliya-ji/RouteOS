// Vite build, dev-server and test configuration.
//
// Deliberately minimal on the build side — the interesting build inputs are the
// VITE_* env vars read in src/api/client.ts, which Vite inlines at build time.
//
// Test config lives here rather than in a separate vitest.config.ts so there is
// one file describing how this project is built and checked. Vitest reads this
// automatically and reuses the React plugin above, which is why the tests need
// no transform setup of their own.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // JSX transform plus fast refresh in dev.
  plugins: [react()],
  server: {
    port: 5173,
    // Listen on every interface, not just localhost. Needed for the dev server
    // to be reachable from outside its own container or VM.
    host: true,
    // NOTE: there is no `proxy` here, so the browser calls the backend's origin
    // directly. What makes local development work is therefore the BACKEND's
    // CORS allow-list, which defaults to localhost:5173 — move this port and
    // BACKEND_CORS_ORIGINS has to follow, or every request is blocked.
  },
  test: {
    // jsdom, not node: the components under test touch document, and client.ts
    // reads localStorage and window.location.
    environment: "jsdom",
    // Makes describe/it/expect available without importing them, matching the
    // backend suite's style.
    globals: true,
    // Runs before each file: installs jest-dom matchers and clears the state
    // that leaks between tests. See src/test/setup.ts.
    setupFiles: ["./src/test/setup.ts"],
    // Only our own tests. Without this, Vitest would also try to collect from
    // node_modules.
    include: ["src/**/*.test.{ts,tsx}"],
    // Report a summary of anything that was not a plain pass, so a skipped
    // file is visible rather than looking like a clean run.
    reporters: "default",
  },
});
