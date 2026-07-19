"""Prometheus metrics."""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

http_requests_total = Counter(
    "routeos_http_requests_total", "Total HTTP requests", ["method", "path", "status"]
)
http_request_latency = Histogram(
    "routeos_http_request_latency_seconds", "HTTP request latency", ["method", "path"]
)
optimization_duration = Histogram(
    "routeos_optimization_duration_seconds", "OR-Tools optimization wall time"
)
active_simulations = Gauge("routeos_active_simulations", "Vehicles currently simulating")
websocket_connections = Gauge("routeos_websocket_connections", "Open WebSocket connections")
