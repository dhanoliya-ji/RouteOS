"""In-process WebSocket connection manager.

Broadcasts fleet events (vehicle moves, status changes, deliveries) to all
connected clients. A single simulation loop is the source of truth; the frontend
never fabricates movement — it only renders what the backend publishes.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket

from app.core.logging import get_logger

logger = get_logger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    @property
    def count(self) -> int:
        return len(self._connections)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)
        logger.info("WebSocket connected (total=%d)", self.count)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)
        logger.info("WebSocket disconnected (total=%d)", self.count)

    async def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        message = {"type": event_type, "data": data}
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001 - client vanished
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)


manager = ConnectionManager()
