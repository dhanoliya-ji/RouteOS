"""Application error type and FastAPI exception handlers producing the
structured error envelope documented in the API spec:

    {"error": {"code": "...", "message": "...", "details": {...}}}

Why one envelope for everything
-------------------------------
A client needs exactly one way to read a failure. Without this, a caller would
have to handle three different shapes — our own errors, FastAPI's validation
errors, and Starlette's bare HTTP errors — and would still be surprised by a
fourth. The three handlers at the bottom translate all of them into the shape
above.

The split of duties matters too:

    services raise APIError        <- "this action is not allowed"
    handlers here build responses  <- "this is what that looks like in HTTP"

That is what keeps business logic free of HTTP concepts, so the same service
function works when called from a background job or a test.

`code` vs `message`
-------------------
`code` is a stable machine-readable token a client may branch on
("ORDER_IMMUTABLE"). `message` is prose for a human and may be reworded freely.
Never branch on the message.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class APIError(Exception):
    """An intentional, expected failure with a client-meaningful explanation.

    Raise this for a rule the caller broke. It carries its own HTTP status, so
    the service decides the semantics ("this is a conflict", "this is missing")
    while the handler below only does the encoding.

    Note the default of 400: a bare APIError means "bad request" unless the
    caller says otherwise.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        # `or {}` so `details` is always a dict — a consumer never has to
        # null-check it, and the envelope shape stays constant.
        self.details = details or {}
        super().__init__(message)


def not_found(resource: str, identifier: Any) -> APIError:
    """Build a consistent 404 for any resource.

    A shorthand used all over the services layer, so every "X does not exist"
    error is worded and coded the same way:

        not_found("order", 42)
        -> code "ORDER_NOT_FOUND", "Order 42 does not exist", HTTP 404

    Deriving the code from the resource name means a new resource gets a
    correctly-shaped error for free, with no constant to add.
    """
    return APIError(
        code=f"{resource.upper()}_NOT_FOUND",
        message=f"{resource.capitalize()} {identifier} does not exist",
        status_code=status.HTTP_404_NOT_FOUND,
        # The id goes in details as data, so a client can use it without
        # parsing it back out of the message text.
        details={"id": identifier},
    )


def _envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """The one place the response shape is defined. Change it here only."""
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the three translators to the app. Called once, from main.py.

    Handlers are matched by exception type, most specific first, so the order
    of registration here does not matter — but the coverage does. Between them
    these three catch every *expected* failure. An unexpected exception is left
    alone deliberately: it should produce a 500 and a logged traceback, not a
    tidy message that hides a bug.
    """

    @app.exception_handler(APIError)
    async def _api_error(_: Request, exc: APIError) -> JSONResponse:
        """Our own errors: everything needed is already on the exception."""
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        """Pydantic rejected the request body, query or path.

        422, not 400: the request was understood but its contents are invalid.
        `exc.errors()` is passed through in `details` because it names the exact
        field and reason for each problem — which is what lets a UI highlight
        the offending input rather than showing a generic failure.
        """
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_envelope("VALIDATION_ERROR", "Request validation failed", {"errors": exc.errors()}),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Errors raised by the framework itself, not by our code.

        Chiefly the 404 for an unknown URL and the 405 for a wrong method.
        These have no domain code of their own, so they share a generic one and
        keep whatever status the framework chose.
        """
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope("HTTP_ERROR", str(exc.detail)),
        )
