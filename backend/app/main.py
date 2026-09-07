"""RouteOS FastAPI application entrypoint.

This module is the *assembly point* of the backend. It creates no business logic
of its own; it wires together pieces built elsewhere, in a specific order:

    1. configure logging          (must happen before anything logs)
    2. create the FastAPI app
    3. add CORS                   (so browsers may call us)
    4. register error handlers    (so every failure shares one shape)
    5. add the request middleware (request id, timing, metrics, access log)
    6. mount every router         (this is where the URL map comes from)
    7. expose /metrics and /

Read it top to bottom and you have the shape of the whole service. For the
layering rules behind it, see app/README.md.
"""
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

# Configure logging FIRST, at import time. Anything that logs during module
# import (a failed setting, a bad DB URL) would otherwise use Python's default
# handler and come out as unstructured text instead of our JSON format.
configure_logging(settings.log_level)
logger = get_logger("routeos")

app = FastAPI(
    title="RouteOS API",
    version="1.0.0",
    description="Intelligent logistics & fleet optimization platform",
    docs_url="/docs",          # interactive API explorer
    openapi_url="/openapi.json",  # the machine-readable schema behind it
)

# --- CORS: which websites are allowed to call this API from a browser --------
#
# Browsers block cross-origin requests unless the server opts in. Our frontend
# is served from a different origin (port 5173 locally, another host in the
# cloud), so without this every request from the UI would fail.
app.add_middleware(
    CORSMiddleware,
    # The explicit allow-list. `settings.cors_origins` normalises a bare
    # hostname into a full origin, because CORS matching is exact.
    allow_origins=settings.cors_origins,
    # Set on managed hosts, where the frontend's exact origin isn't known at
    # deploy time (see Settings.backend_cors_origin_regex). Empty locally.
    allow_origin_regex=settings.backend_cors_origin_regex or None,
    # Required for the Authorization header to be sent cross-origin.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Teach the app how to turn our exceptions into the shared error envelope,
# {"error": {"code", "message", "details"}}. Registered before any request is
# served, so no failure can escape unshaped. See core/errors.py.
register_exception_handlers(app)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Wrap every request: tag it, time it, count it, log it.

    Runs for *all* requests, so this is the one place that sees each one both
    before and after the handler. Four jobs, in order:
    """
    # 1. Give the request an id and publish it to the logging ContextVar, so
    #    any log line written deeper in the call stack carries the same id
    #    without having to be passed it. A ContextVar (not a global) is what
    #    keeps ids from bleeding between concurrent requests.
    request_id = new_request_id()
    request_id_ctx.set(request_id)

    # 2. Time the handler. perf_counter is monotonic, so it cannot go backwards
    #    if the system clock is adjusted mid-request.
    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    path = request.url.path
    # Echo the id back so a client (or a support ticket) can quote it and we
    # can find every log line for that exact request.
    response.headers["X-Request-ID"] = request_id

    # 3. Record Prometheus metrics. Wrapped in try/except because observability
    #    must never be the reason a request fails — a bad label value should
    #    lose us a data point, not the response.
    try:
        http_requests_total.labels(request.method, path, response.status_code).inc()
        http_request_latency.labels(request.method, path).observe(latency_ms / 1000.0)
    except Exception:  # noqa: BLE001
        pass

    # 4. One structured access-log line per request.
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


# --- The URL map -------------------------------------------------------------
#
# Every router declares only its own prefix ("/orders", "/auth", ...). The API
# version is applied here, so it lives in exactly one place: bumping to /api/v2
# is a change to this constant, not to twelve route modules.
API = settings.api_v1_prefix

# health is mounted WITHOUT the version prefix: a load balancer probes /health,
# and that URL should not move when the API version does.
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

# Also unversioned: WebSocket clients connect to a fixed /ws/fleet URL.
app.include_router(ws.router)


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus scrape endpoint.

    Counters and histograms are updated as events happen (in the middleware
    above). The two *gauges* below describe a current value rather than a
    running total, so they are sampled here, at scrape time — reading them from
    the live objects is both cheaper and more accurate than trying to keep them
    updated on every connect/disconnect.
    """
    active_simulations.set(sim_engine.active_count)
    websocket_connections.set(manager.count)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/")
async def root():
    """Signpost for anyone who opens the bare URL."""
    return {"service": "RouteOS", "docs": "/docs", "health": "/health"}
