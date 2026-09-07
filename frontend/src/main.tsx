// Application bootstrap.
//
// The only file that touches the DOM directly. Its job is to mount React and
// install the three providers everything else assumes are present — nothing
// here knows anything about RouteOS itself.
import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

// The server-state cache shared by every useQuery/useMutation in the app.
//
// Created once, at module scope. Building it inside a component would throw the
// whole cache away on re-render.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Retry once, not the default three. Three attempts against a backend
      // that is actually down just delays the error the user needs to see.
      retry: 1,
      // Off. The default refetches every mounted query whenever the tab
      // regains focus, which turns alt-tabbing into a burst of requests on a
      // screen with several queries.
      refetchOnWindowFocus: false,
      // Treat data as fresh for 10s, so navigating away and back within that
      // window serves from cache instead of refetching.
      //
      // Worth knowing when something looks stale: a change made elsewhere can
      // take up to 10s to appear. Mutations call invalidateQueries, which
      // bypasses this entirely — that is the mechanism that keeps a screen
      // correct straight after a write.
      staleTime: 10_000,
    },
  },
});

// The `!` is a deliberate assertion: index.html always contains <div id="root">,
// so a null here would mean the HTML shell is broken — which should fail loudly
// at startup rather than be handled.
ReactDOM.createRoot(document.getElementById("root")!).render(
  // StrictMode is development-only and intentionally double-invokes effects to
  // surface missing cleanup. That is why useFleetSocket's teardown is
  // load-bearing rather than theoretical — see hooks/README.md.
  <React.StrictMode>
    {/* Must wrap anything calling useQuery. */}
    <QueryClientProvider client={queryClient}>
      {/* Must wrap anything using routes, links or navigation. */}
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
