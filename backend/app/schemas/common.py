"""Response shapes reused across every resource.

Four small models that exist so the same structures are not redefined per
endpoint — and so a client can learn them once.
"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

# The item type a Page holds. Declaring it as a TypeVar (rather than typing
# items as list[Any]) is what keeps Page[OrderOut] fully typed: OpenAPI then
# generates a distinct schema per instantiation, so /docs shows the real item
# shape instead of a vague list.
T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """One page of a longer list, plus what a client needs to page through it.

    Written once, used as Page[OrderOut], Page[VehicleOut], and so on.

    Both `total` and `pages` are returned deliberately: `total` is the row
    count, `pages` is ceil(total / page_size). A client needs `pages` to render
    a pager, and computing it client-side means two places can disagree about
    the arithmetic — so the server states it.
    """

    items: list[T]
    total: int       # matching rows in the whole result set, not just this page
    page: int        # 1-based, matching the query parameter
    page_size: int
    pages: int       # total number of pages available


class ErrorDetail(BaseModel):
    """The inner half of the error envelope from core/errors.py."""

    # A stable machine-readable token ("ORDER_IMMUTABLE"). Safe to branch on.
    code: str
    # Prose for a human. May be reworded at any time — never branch on it.
    message: str
    # Structured extras: the offending id, the failing fields. Defaults to an
    # empty dict so a consumer never has to null-check it.
    details: dict[str, Any] = {}


class ErrorResponse(BaseModel):
    """The full error envelope: {"error": {...}}.

    Declared here so it can be referenced in OpenAPI documentation. The actual
    responses are built by the handlers in core/errors.py, which is why this
    model is never instantiated in application code.
    """

    error: ErrorDetail


class Message(BaseModel):
    """A bare acknowledgement, for endpoints with nothing else to return.

    Used by DELETE and the discard endpoint. A response body of `{"message":
    "..."}` rather than an empty 204, so every endpoint returns JSON and a
    client needs only one code path.
    """

    message: str
