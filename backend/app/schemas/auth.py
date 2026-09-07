"""Registration, login and user-facing shapes.

The important property of this file is what `UserOut` does NOT contain.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import UserRole


class UserRegister(BaseModel):
    """Creating an account."""

    name: str = Field(min_length=1, max_length=120)
    # EmailStr actually validates the address shape rather than accepting any
    # string. It requires the `email-validator` package, which is why that sits
    # in requirements.txt — without it this import fails loudly at startup
    # rather than silently accepting anything.
    email: EmailStr
    # A floor of 8, and a ceiling: bcrypt only considers the first 72 bytes of
    # input, so accepting unbounded passwords would silently ignore the tail
    # and also let a caller post megabytes to be hashed.
    password: str = Field(min_length=8, max_length=128)
    # Defaults to the least privilege. Note this endpoint lets a caller ask for
    # a role — acceptable for a demo with open registration, but in a real
    # deployment role assignment belongs behind an admin-only endpoint.
    role: UserRole = UserRole.VIEWER


class UserLogin(BaseModel):
    """Email + password.

    NOTE: not actually used by the login endpoint. `/auth/login` takes an OAuth2
    password-flow *form* (via OAuth2PasswordRequestForm) rather than a JSON
    body, because that is what makes the Authorize button in /docs work. This
    model documents the logical shape and is a ready-made body schema if a JSON
    login is ever added.
    """

    email: EmailStr
    password: str


class Token(BaseModel):
    """What a successful login returns.

    `token_type` is fixed at "bearer" and included because the OAuth2 spec says
    a token response carries it — clients are expected to read it rather than
    assume.
    """

    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    """A user, as the API is willing to describe one.

    There is no `password_hash` field, and that is the point of writing this
    model out by hand instead of serialising the ORM object: the hash cannot
    leak, because it was never part of the response shape. Same for any
    sensitive column added to the model later — it will not appear here unless
    someone deliberately adds it.
    """

    # Read attributes off a SQLAlchemy row rather than requiring a dict, so a
    # handler can `return user` directly.
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: EmailStr
    role: UserRole
    is_active: bool
    created_at: datetime
