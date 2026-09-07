// Vite build and dev-server configuration.
//
// Deliberately minimal — the interesting build inputs are the VITE_* env vars
// read in src/api/client.ts, which Vite inlines at build time.
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
});
