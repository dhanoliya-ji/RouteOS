# `src/hooks/` — custom React hooks

One hook: `useFleetSocket`. It is the frontend's entire live-data path.

```
   backend simulation engine
        │  broadcasts an event
        ▼
   WebSocket  /ws/fleet
        │
   useFleetSocket(onEvent)
        │  calls your handler, once per event
        ▼
   LiveOps        vehicle positions, the event feed, traffic warnings
   RoutePlanner   solver progress heartbeats
```

Two screens use it, for different events from the same stream.

---

## Why this is a hook and not a store

The events are a *stream*, not a resource. There is no URL to re-request "the
current positions" from, and no cache to invalidate — so neither TanStack Query
nor Zustand fits. A hook that hands each event to a callback lets each screen
keep only the slice it cares about in its own `useState`.

It also means the two consumers are independent: `LiveOps` tracking positions
does not make `RoutePlanner` re-render, because they hold separate state.

---

## The three problems it solves

Everything in the file is one of these.

### 1. Connect once, not on every render

```js
useEffect(() => { ...connect... }, []);   // empty deps
```

An empty dependency array means one socket per mount. But the effect needs
`onEvent`, and a page passes a **new function object every render** (it is
defined inline). Listing it as a dependency would tear down and reopen the
socket on every render — a reconnect loop.

### 2. …while still calling the *latest* handler

Solved with a ref rather than a dependency:

```js
const handlerRef = useRef(onEvent);
handlerRef.current = onEvent;        // updated every render
...
ws.onmessage = (msg) => handlerRef.current(JSON.parse(msg.data));
```

The socket is created once and reads `handlerRef.current` at delivery time, so
it always invokes the current closure — which matters because the handler
closes over state (`jobId` in `RoutePlanner`), and a stale closure would compare
against an outdated value.

This is the standard "latest ref" pattern. Note the assignment happens during
render rather than in an effect, which is a mutation during render — acceptable
here because it targets a ref (not React state) and so triggers nothing.

### 3. Reconnect, but stop when told

```js
let closed = false;

ws.onclose = () => {
  setConnected(false);
  if (!closed) retry = setTimeout(connect, 2000);
};

return () => { closed = true; clearTimeout(retry); ws?.close(); };
```

`closed` is the important flag. Calling `ws.close()` in the cleanup **fires
`onclose`**, which would schedule a reconnect for a component that is going
away. The flag makes the cleanup's intent distinguishable from a dropped
connection.

Both are needed: `closed` stops a *future* retry being scheduled, and
`clearTimeout` cancels one already pending.

---

## What it returns

```js
const { connected } = useFleetSocket(onEvent);
```

Just a boolean, which `LiveOps` renders as the green/red dot in its header. That
indicator is worth having: without it, a dead socket and an idle simulation look
identical — a still map either way.

---

## Limits worth knowing

- **A fixed 2-second retry**, with no backoff and no cap. If the backend is
  down, this reconnects every two seconds indefinitely. Exponential backoff is
  the usual improvement.
- **Malformed messages are swallowed** — `JSON.parse` failures hit an empty
  `catch`, so a bad frame is ignored rather than breaking the stream. Correct
  behaviour, but it means a genuinely broken payload is silent.
- **No authentication.** A browser cannot set an `Authorization` header on a
  WebSocket handshake, so the connection is unauthenticated; see the backend's
  `app/api/README.md` for the ticket-based fix.
- **Events are not buffered.** A client that reconnects misses whatever happened
  while it was away. The backend compensates by sending a `SNAPSHOT` on
  connect, which re-establishes current state — so a reconnect recovers
  positions, but the event feed keeps a gap.
