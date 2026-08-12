"""RouteOS FastAPI application entrypoint."""
from __future__ import annotations

import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.routes import (
    analytics,
    auth,
    dashboard,
    depots,
    health,
    optimization,
    orders,
    routes,
    simulation,
    users,
    vehicles,
    ws,
)
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger, new_request_id, request_id_ctx
from app.core.metrics import (
    active_simulations,
    http_request_latency,
    http_requests_total,
    websocket_connections,
)
from app.simulation.engine import engine as sim_engine
from app.websocket.manager import manager

configure_logging(settings.log_level)
logger = get_logger("routeos")

app = FastAPI(
    title="RouteOS API",
    version="1.0.0",
    description="Intelligent logistics & fleet optimization platform",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Set on managed hosts, where the frontend's exact origin isn't known at
    # deploy time (see Settings.backend_cors_origin_regex). Empty locally.
    allow_origin_regex=settings.backend_cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = new_request_id()
    request_id_ctx.set(request_id)
    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    path = request.url.path
    response.headers["X-Request-ID"] = request_id
    try:
        http_requests_total.labels(request.method, path, response.status_code).inc()
        http_request_latency.labels(request.method, path).observe(latency_ms / 1000.0)
    except Exception:  # noqa: BLE001
        pass
    logger.info(
        "request",
        extra={
            "method": request.method,
            "path": path,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
        },
    )
    return response


API = settings.api_v1_prefix
app.include_router(health.router)
app.include_router(auth.router, prefix=API)
app.include_router(users.router, prefix=API)
app.include_router(depots.router, prefix=API)
app.include_router(orders.router, prefix=API)
app.include_router(vehicles.router, prefix=API)
app.include_router(routes.router, prefix=API)
app.include_router(optimization.router, prefix=API)
app.include_router(simulation.router, prefix=API)
app.include_router(analytics.router, prefix=API)
app.include_router(dashboard.router, prefix=API)
app.include_router(ws.router)


@app.get("/metrics")
async def metrics() -> Response:
    active_simulations.set(sim_engine.active_count)
    websocket_connections.set(manager.count)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/")
async def root():
    return {"service": "RouteOS", "docs": "/docs", "health": "/health"}
