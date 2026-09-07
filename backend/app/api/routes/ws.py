"""The WebSocket endpoint.

The one route module that does not follow the declare/validate/authorize/
delegate shape, because a socket is not a request/response. There is nothing to
validate and nothing to return — the handler's whole job is to register the
client and then stay out of the way.

Mounted WITHOUT the /api/v1 prefix (see main.py), so the connection URL is
stable across API versions.
"""
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
    # Accepts the handshake and adds the socket to the broadcast set.
    await manager.connect(ws)
    try:
        # Snapshot first, then stream.
        #
        # A client joining mid-simulation would otherwise know nothing until the
        # next event happened to fire — so a map would open empty and fill in
        # gradually. Sending current state immediately means the UI can draw the
        # whole fleet at once and then apply deltas.
        await ws.send_json({"type": "SNAPSHOT", "data": engine.status()})

        while True:
            # We don't require inbound messages; this keeps the socket open and
            # lets the client send pings if it wants.
            #
            # The await is the point: it suspends until the client sends
            # something or disconnects. Without a blocking read the handler
            # would return and the framework would close the connection. The
            # received text is deliberately discarded — this stream is
            # server-to-client only, and accepting commands here would be an
            # unauthenticated control channel (see the auth note below).
            await ws.receive_text()
    except WebSocketDisconnect:
        # The normal ending: the browser closed the tab or navigated away.
        await manager.disconnect(ws)
    except Exception:  # noqa: BLE001
        # Anything else — a network drop, a client vanishing without a close
        # frame — must still deregister the socket, or the manager would keep
        # trying to send to it forever.
        await manager.disconnect(ws)


# NOTE: this endpoint is unauthenticated.
#
# A browser cannot set an Authorization header on a WebSocket handshake, so the
# usual Depends(get_current_user) cannot apply. The stream is read-only fleet
# telemetry and accepts no commands, which limits the exposure, but it is a real
# gap: anyone who can reach the port can watch vehicle positions.
#
# The standard fix is a short-lived ticket — the client calls an authenticated
# endpoint for a single-use token and passes it as a query parameter, which the
# handler validates before manager.connect().
