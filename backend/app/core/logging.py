"""Structured JSON logging + request-id context.

Two ideas in this file:

1. **Logs are JSON, one object per line.** A log aggregator can then filter on
   fields (`status_code >= 500`, `latency_ms > 1000`) instead of matching text
   with regexes. The cost is that a line is less pleasant to read by eye.

2. **Every line carries a request id**, without anyone passing it around. A
   ContextVar makes the current request's id visible to code arbitrarily deep
   in the call stack, so all the lines for one request can be grouped after the
   fact.

Typical output:

    {"ts":"2026-01-14T10:22:31","level":"INFO","logger":"routeos",
     "request_id":"a3f9c1d20b74","message":"request","method":"POST",
     "path":"/api/v1/orders","status_code":201,"latency_ms":42.7}
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar

# The current request's id.
#
# A ContextVar rather than a module-level global: under asyncio many requests
# are in flight at once, and each needs its own value. A global would be
# overwritten by whichever request started most recently, so log lines would be
# attributed to the wrong request. ContextVars are per-task, so they don't.
#
# The default "-" is what unrelated logs get — startup, background jobs, the
# simulation tick — since none of those belong to a request.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id() -> str:
    """A short random id for one request.

    12 hex characters (48 bits) from a UUID4. Not full length because this is
    only for correlating log lines within a modest retention window, and a
    shorter id is easier to quote in a support ticket or paste into a search.
    """
    return uuid.uuid4().hex[:12]


class JsonFormatter(logging.Formatter):
    """Renders a LogRecord as a single line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        # The fields every line gets, whatever it is about.
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            # Read at format time, so the value is whichever request is
            # currently in scope. This is the whole point of the ContextVar.
            "request_id": request_id_ctx.get(),
            "message": record.getMessage(),
        }

        # Optional fields, copied across only when the caller supplied them via
        # logger.info(..., extra={...}). An allow-list rather than "copy
        # everything unknown from the record", because a LogRecord carries
        # dozens of internal attributes we do not want in the output.
        for key in ("method", "path", "status_code", "latency_ms", "optimization_run_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)

        # logger.exception() / exc_info=True: fold the traceback into the same
        # object, so a stack trace stays attached to its line rather than
        # becoming a dozen unparseable lines of its own.
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # default=str so an unexpected value (a datetime, an enum, a model)
        # degrades to its string form instead of raising inside the logger —
        # logging must never be the thing that breaks a request.
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter as the process-wide logging setup.

    Called once, at import time, from main.py.
    """
    # stdout, not a file: in a container, logs are collected from the process's
    # output stream. Writing files would mean nobody sees them.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    # Clear first. uvicorn and others install their own handlers, and without
    # this every line would be emitted twice — once as JSON, once as their text.
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Quiet noisy libraries
    #
    # uvicorn.access duplicates the access log our own middleware already
    # writes (with a request id attached), and sqlalchemy.engine at INFO logs
    # every statement, which buries everything else.
    for noisy in ("uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger.

    A thin wrapper over the stdlib so callers don't import `logging` directly —
    which keeps the option open to change the logging backend in one place.
    Callers pass __name__, so the module path shows up in the "logger" field.
    """
    return logging.getLogger(name)
