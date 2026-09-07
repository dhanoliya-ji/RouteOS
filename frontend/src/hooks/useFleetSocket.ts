import { useEffect, useRef, useState } from "react";
import { WS_BASE } from "../api/client";
import type { WsEvent } from "../types";

/**
 * Subscribe to the backend fleet WebSocket. The backend is the single source of
 * truth for vehicle movement; this hook only relays events to the UI.
 *
 * A hook rather than a store because these events are a *stream*: there is no
 * URL to re-request "current positions" from and no cache to invalidate, so
 * neither TanStack Query nor Zustand fits. Each caller keeps only the slice it
 * cares about in its own state, which is also why LiveOps and RoutePlanner can
 * both use this without re-rendering each other.
 *
 * Everything in the body solves one of three problems: connect once, always
 * call the latest handler, and reconnect without reconnecting on the way out.
 */
export function useFleetSocket(onEvent: (e: WsEvent) => void) {
  const [connected, setConnected] = useState(false);

  // PROBLEM 2 — always call the latest handler.
  //
  // The effect below must not depend on `onEvent`: callers pass an inline
  // arrow function, so it is a new object every render, and listing it as a
  // dependency would tear down and reopen the socket in a loop.
  //
  // But the socket still needs the CURRENT handler, because it closes over
  // state — RoutePlanner's compares against `jobId`, and a stale closure would
  // test an outdated value. So the handler goes in a ref, read at delivery
  // time. This is the standard "latest ref" pattern.
  //
  // The assignment happens during render rather than in an effect. That is a
  // mutation during render, which is acceptable only because it targets a ref
  // (not state) and so triggers nothing.
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout>;
    // PROBLEM 3 — distinguish "we are unmounting" from "the connection
    // dropped". Both arrive as an onclose event; only the second should
    // reconnect.
    let closed = false;

    const connect = () => {
      ws = new WebSocket(`${WS_BASE}/ws/fleet`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        // Guarded by `closed`: the cleanup below calls ws.close(), which FIRES
        // this handler — without the flag we would schedule a reconnect for a
        // component that is going away.
        if (!closed) retry = setTimeout(connect, 2000);
      };
      ws.onmessage = (msg) => {
        try {
          // Read through the ref, so this always invokes the current closure.
          handlerRef.current(JSON.parse(msg.data) as WsEvent);
        } catch {
          /* ignore malformed */
          // A bad frame is dropped rather than breaking the stream. Correct,
          // but it does mean a genuinely broken payload fails silently.
        }
      };
    };
    connect();

    return () => {
      // Order matters: set the flag first so the ws.close() below cannot
      // schedule a retry, then cancel any retry already pending, then close.
      closed = true;
      clearTimeout(retry);
      ws?.close();
    };
    // PROBLEM 1 — empty deps, so exactly one socket per mount. See the ref above.
    //
    // Note React StrictMode mounts, unmounts and remounts in development, so
    // this cleanup runs for real on every dev startup. That is what makes it
    // load-bearing rather than theoretical.
  }, []);

  // Surfaced so LiveOps can show its green/red indicator. Worth having: without
  // it, a dead socket and an idle simulation look identical — a still map
  // either way.
  //
  // Note the retry is a flat 2s with no backoff and no cap, so a backend that
  // is down is reconnected against indefinitely.
  return { connected };
}
