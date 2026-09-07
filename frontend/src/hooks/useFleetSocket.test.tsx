/**
 * Tests for the live-data hook.
 *
 * Every line of this hook solves one of three problems — connect once, always
 * call the latest handler, reconnect without reconnecting on the way out — and
 * all three fail in ways that are hard to see by hand: a reconnect loop looks
 * like nothing, and a stale closure looks like "the progress bar sometimes
 * doesn't move".
 *
 * A fake WebSocket stands in for the real one, so no server is involved and
 * connection lifecycle is under the test's control.
 */
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useFleetSocket } from "./useFleetSocket";

/** Every socket the code under test opened, in order. */
let sockets: FakeSocket[] = [];

class FakeSocket {
  static OPEN = 1;
  url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    sockets.push(this);
  }

  close() {
    this.closed = true;
    // The real WebSocket fires onclose when you close it, which is exactly the
    // behaviour the hook's `closed` flag has to distinguish from a drop.
    this.onclose?.();
  }

  /* -- helpers for the test to drive the connection -- */
  open() {
    this.onopen?.();
  }
  drop() {
    this.onclose?.();
  }
  send(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
  sendRaw(data: string) {
    this.onmessage?.({ data });
  }
}

beforeEach(() => {
  sockets = [];
  vi.stubGlobal("WebSocket", FakeSocket as unknown as typeof WebSocket);
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

const last = () => sockets[sockets.length - 1];

describe("connecting", () => {
  it("opens exactly one socket, at the fleet endpoint", () => {
    renderHook(() => useFleetSocket(() => {}));
    expect(sockets).toHaveLength(1);
    expect(last().url).toMatch(/\/ws\/fleet$/);
  });

  it("reports connected only once the socket is open", async () => {
    const { result } = renderHook(() => useFleetSocket(() => {}));
    expect(result.current.connected).toBe(false);

    act(() => last().open());
    await waitFor(() => expect(result.current.connected).toBe(true));
  });

  it("does NOT reopen on re-render", () => {
    /**
     * The reconnect loop this hook is built to avoid. Callers pass an inline
     * arrow function, so `onEvent` is a new object every render — listing it
     * as an effect dependency would tear down and reopen the socket forever,
     * which presents as a silently dead connection rather than an error.
     */
    const { rerender } = renderHook(() => useFleetSocket(() => {}));
    rerender();
    rerender();
    rerender();
    expect(sockets).toHaveLength(1);
  });
});

describe("delivering events", () => {
  it("parses a frame and hands it to the handler", () => {
    const onEvent = vi.fn();
    renderHook(() => useFleetSocket(onEvent));

    act(() => last().send({ type: "VEHICLE_LOCATION_UPDATED", data: { vehicle_id: 7 } }));

    expect(onEvent).toHaveBeenCalledWith({
      type: "VEHICLE_LOCATION_UPDATED",
      data: { vehicle_id: 7 },
    });
  });

  it("calls the LATEST handler, not the one from the first render", () => {
    /**
     * The whole reason for the latest-ref. RoutePlanner's handler closes over
     * `jobId` and compares incoming events against it; a stale closure would
     * test an outdated value and silently drop every progress event — the
     * progress bar would simply never move.
     */
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(({ h }) => useFleetSocket(h), {
      initialProps: { h: first },
    });

    rerender({ h: second });
    act(() => last().send({ type: "PING", data: {} }));

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledOnce();
  });

  it("ignores a malformed frame without breaking the stream", () => {
    const onEvent = vi.fn();
    renderHook(() => useFleetSocket(onEvent));

    act(() => last().sendRaw("{ not json"));
    // Dropped silently...
    expect(onEvent).not.toHaveBeenCalled();

    // ...and the next good frame still arrives.
    act(() => last().send({ type: "OK", data: {} }));
    expect(onEvent).toHaveBeenCalledOnce();
  });
});

describe("reconnecting", () => {
  it("reopens two seconds after the connection drops", () => {
    renderHook(() => useFleetSocket(() => {}));
    act(() => last().open());

    act(() => last().drop());
    expect(sockets).toHaveLength(1); // not yet

    act(() => void vi.advanceTimersByTime(2000));
    expect(sockets).toHaveLength(2);
  });

  it("marks itself disconnected while it is down", async () => {
    // The indicator LiveOps shows. Without it a dead socket and an idle
    // simulation look identical — a still map either way.
    const { result } = renderHook(() => useFleetSocket(() => {}));
    act(() => last().open());
    await waitFor(() => expect(result.current.connected).toBe(true));

    act(() => last().drop());
    await waitFor(() => expect(result.current.connected).toBe(false));
  });

  it("does NOT reconnect after unmount", () => {
    /**
     * The `closed` flag. Cleanup calls ws.close(), which fires onclose — so
     * without the flag, unmounting would schedule a reconnect for a component
     * that is going away, leaving an orphaned socket reopening forever.
     */
    const { unmount } = renderHook(() => useFleetSocket(() => {}));
    act(() => last().open());

    unmount();
    act(() => void vi.advanceTimersByTime(10_000));

    expect(sockets).toHaveLength(1);
  });

  it("closes the socket on unmount", () => {
    const { unmount } = renderHook(() => useFleetSocket(() => {}));
    const socket = last();
    unmount();
    expect(socket.closed).toBe(true);
  });

  it("cancels a retry that was already pending", () => {
    // Both guards are needed: the flag stops a FUTURE retry being scheduled,
    // clearTimeout cancels one already in flight.
    const { unmount } = renderHook(() => useFleetSocket(() => {}));
    act(() => last().drop());          // retry now scheduled

    unmount();
    act(() => void vi.advanceTimersByTime(10_000));

    expect(sockets).toHaveLength(1);
  });

  it("keeps retrying while the backend stays down", () => {
    // A fixed 2s interval with no backoff and no cap — recorded as the
    // current behaviour, and the obvious thing to improve.
    renderHook(() => useFleetSocket(() => {}));
    for (let i = 0; i < 3; i++) {
      act(() => last().drop());
      act(() => void vi.advanceTimersByTime(2000));
    }
    expect(sockets).toHaveLength(4);
  });
});

describe("two consumers stay independent", () => {
  it("gives each its own socket and handler", () => {
    /**
     * LiveOps and RoutePlanner both subscribe, for different events. Each
     * keeps only the slice it cares about, which is why this is a hook rather
     * than a shared store.
     */
    const a = vi.fn();
    const b = vi.fn();
    function Two() {
      useFleetSocket(a);
      useFleetSocket(b);
      return <p>two subscribers</p>;
    }
    render(<Two />);
    expect(screen.getByText("two subscribers")).toBeInTheDocument();
    expect(sockets).toHaveLength(2);

    act(() => sockets[0].send({ type: "X", data: {} }));
    expect(a).toHaveBeenCalledOnce();
    expect(b).not.toHaveBeenCalled();
  });
});
