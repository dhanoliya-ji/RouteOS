"""In-process WebSocket connection manager.

Broadcasts fleet events (vehicle moves, status changes, deliveries) to all
connected clients. A single simulation loop is the source of truth; the frontend
never fabricates movement — it only renders what the backend publishes.

This is the *transport*, and nothing else: it generates no events of its own.
The simulation engine and the optimization service produce them and call in
here to publish, which is the publish/subscribe pattern — a publisher does not
know who is listening, and a listener asks for nothing.

Note the layering direction: this module sits BELOW its publishers and imports
none of them. They import it. That is what keeps it a dumb pipe with no
knowledge of routing or solving, and what prevents an import cycle.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket

from app.core.logging import get_logger

logger = get_logger(__name__)


class ConnectionManager:
    """The set of live client connections, plus a fan-out send."""

    def __init__(self) -> None:
        # A set, because membership is the only question ever asked: add,
        # discard, iterate. No ordering is needed and duplicates are impossible.
        self._connections: set[WebSocket] = set()
        # Guards mutations of that set. Needed because connects, disconnects and
        # broadcasts all run concurrently on the event loop, and a set must not
        # be modified from two places at once.
        self._lock = asyncio.Lock()

    @property
    def count(self) -> int:
        """How many clients are listening.

        Read WITHOUT the lock, deliberately. `len()` on a set is atomic enough
        for a gauge: the number may be one stale by the time it is serialised,
        which is fine for /health and /metrics and not worth contending the
        lock for on every scrape.
        """
        return len(self._connections)

    async def connect(self, ws: WebSocket) -> None:
        """Accept the handshake and register the client.

        `accept()` must come first — until the handshake completes there is no
        connection to send on, so registering earlier could put a socket in the
        broadcast set that is not yet usable.
        """
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)
        logger.info("WebSocket connected (total=%d)", self.count)

    async def disconnect(self, ws: WebSocket) -> None:
        """Deregister a client.

        `discard`, not `remove`: discard is a no-op when the socket is already
        gone, and this can legitimately be called twice — once by the endpoint's
        exception handler and once after broadcast reaped it as dead.
        """
        async with self._lock:
            self._connections.discard(ws)
        logger.info("WebSocket disconnected (total=%d)", self.count)

    async def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """Send one event to every connected client.

        Every message shares the same two-key envelope, so a client is a single
        switch on `type`. Adding a new event never changes the shape, which
        means an old client ignores it rather than breaking on it.

        Every event published here is a fact that ALREADY happened — the
        database write precedes the broadcast — so a client can trust it
        without confirming.
        """
        message = {"type": event_type, "data": data}
        dead: list[WebSocket] = []

        # Iterate a COPY. A client can disconnect while this loop is running,
        # mutating the set — and iterating a live set that changes size raises
        # RuntimeError.
        for ws in list(self._connections):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001 - client vanished
                # Catch PER RECIPIENT, which is the important part: a browser
                # closed without a proper close frame only surfaces as a failed
                # send. Without this, one stale socket would abort the whole
                # broadcast and freeze every other client's map.
                #
                # Collected rather than removed here, for the same reason the
                # loop iterates a copy: you cannot discard from a set you are
                # walking.
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)


# The one manager for this process.
#
# Imported by the engine, both services, health.py, ws.py and main.py, so all of
# them fan out to the same set of clients.
#
# This is also the scaling limit: the set lives in THIS process, so broadcast()
# reaches only clients attached to this instance. A second replica would talk to
# its own clients and no others. Fan-out across instances needs a message bus —
# Redis is already a dependency and its pub/sub is the obvious next step. See
# app/websocket/README.md.
manager = ConnectionManager()
