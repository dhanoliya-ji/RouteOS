from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.simulation.engine import engine
from app.websocket.manager import manager

router = APIRouter()


@router.websocket("/ws/fleet")
async def fleet_ws(ws: WebSocket) -> None:
    """Live fleet event stream. Clients receive a snapshot on connect, then a
    push for every VEHICLE_LOCATION_UPDATED / ORDER_STATUS_UPDATED /
    ROUTE_* event emitted by the simulation engine."""
    await manager.connect(ws)
    try:
        await ws.send_json({"type": "SNAPSHOT", "data": engine.status()})
        while True:
            # We don't require inbound messages; this keeps the socket open and
            # lets the client send pings if it wants.
            await ws.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(ws)
    except Exception:  # noqa: BLE001
        await manager.disconnect(ws)
