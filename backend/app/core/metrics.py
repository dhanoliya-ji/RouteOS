"""Prometheus metrics.

Five metrics, scraped as text from ``GET /metrics``. Prometheus pulls on an
interval; the app never pushes.

Choosing the right metric *type* is most of the work, because the type
determines what queries are possible later:

    Counter    only ever goes up. You query its RATE ("requests per second"),
               never its value. Restart-safe: a reset is detectable.

    Histogram  bucketed observations. Lets you ask for percentiles
               ("p99 latency"), which an average cannot give you — an average
               hides the slow tail entirely.

    Gauge      a value that goes up and down, read as-is ("how many right
               now"). Meaningless to rate.

Counters and histograms below are updated as events happen, in main.py's
middleware. The two gauges are sampled at scrape time instead, because they
describe a current state that is cheaper to read than to track.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Request volume. The labels are what make it useful: you can slice by status
# to chart an error rate, or by path to find which endpoint is being hammered.
#
# Careful with labels in general — every distinct combination is a separate
# stored series. These three are safe because methods and statuses are small
# fixed sets and `path` is the route *template* ("/orders/{id}"), not the
# concrete URL. Labelling by raw URL would create a new series per order id.
http_requests_total = Counter(
    "routeos_http_requests_total", "Total HTTP requests", ["method", "path", "status"]
)

# Latency distribution, in seconds (Prometheus convention is base units, so
# seconds not milliseconds — the middleware divides by 1000 before observing).
# A histogram rather than a gauge or average so p95/p99 can be computed.
http_request_latency = Histogram(
    "routeos_http_request_latency_seconds", "HTTP request latency", ["method", "path"]
)

# How long solves take. Unlabelled: there is one solver, and run size varies so
# much that a single distribution is more honest than a misleading average.
optimization_duration = Histogram(
    "routeos_optimization_duration_seconds", "OR-Tools optimization wall time"
)

# --- Gauges: sampled in main.py's /metrics handler, not updated on events ----
# Tracking these incrementally would mean an inc/dec at every start, stop,
# completion and disconnect — several places to keep in sync, each a chance to
# leak the count. Reading the live objects once per scrape cannot drift.
active_simulations = Gauge("routeos_active_simulations", "Vehicles currently simulating")
websocket_connections = Gauge("routeos_websocket_connections", "Open WebSocket connections")
