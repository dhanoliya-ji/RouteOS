"""Authentication & authorization dependencies.

Two questions, one per public function:

    get_current_user   who is calling?      -> 401 if we cannot tell
    require_roles(...)  may they do this?    -> 403 if not

Both are FastAPI *dependencies*: declared in a handler's signature and run
before its body. That is what makes a permission impossible to forget silently —
it is part of the signature, so reading a handler tells you who may call it —
and it is why an unauthorised request never reaches application code.
"""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import APIError
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User

# Extracts the bearer token from the Authorization header.
#
# auto_error=False is deliberate and load-bearing. By default this scheme raises
# its own HTTPException the moment the header is missing — before our code runs —
# producing a bare {"detail": "Not authenticated"} that bypasses the error
# envelope in core/errors.py. Turning it off makes the token None instead, so
# get_current_user can raise APIError and an auth failure looks like every other
# error the API returns.
#
# tokenUrl is not used for validation; it tells /docs where to POST credentials,
# which is what makes the Authorize button work.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/login", auto_error=False)

# Built once and re-raised, rather than constructed per rejection. They carry no
# per-request state, so there is nothing to vary — and it keeps the wording
# identical everywhere each is raised.
#
# The 401/403 split is worth preserving carefully:
#   401 = we do not know who you are      -> the client should log in
#   403 = we know, and you may not        -> logging in again will not help
# Collapsing them into one status is a common shortcut that leaves a client
# unable to tell an expired session from a permissions problem.
_UNAUTH = APIError("NOT_AUTHENTICATED", "Authentication required", status_code=401)
_FORBIDDEN = APIError("FORBIDDEN", "You do not have permission to perform this action", status_code=403)


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the bearer token to a live, active User.

    Four gates, each rejecting with 401:
      1. a token was sent at all
      2. its signature and expiry check out
      3. the user it names still exists
      4. that user is still active
    """
    if not token:
        raise _UNAUTH

    try:
        payload = decode_access_token(token)
        # "sub" is a string per the JWT spec; our ids are integers.
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        # Three narrow types, each a real failure mode:
        #   PyJWTError -> bad signature, malformed token, or expired
        #   KeyError   -> no "sub" claim
        #   ValueError -> "sub" is not an integer
        # Deliberately NOT a bare except: that would also swallow a genuine bug
        # in the code below and report it to the client as "invalid
        # credentials", which is an expensive thing to debug.
        #
        # `from exc` keeps the original traceback chained for the logs while the
        # client sees only the generic message — no detail about *why* the token
        # was rejected, which would help someone probing it.
        raise APIError("INVALID_TOKEN", "Could not validate credentials", status_code=401) from exc

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()

    # Why re-read the user at all, when the token already carries the id and
    # role: a JWT stays valid until it expires, and that is 24 hours by default.
    # Without this lookup, deactivating an account would leave it working for up
    # to a day — so the is_active check IS the revocation mechanism. It costs
    # one indexed primary-key read per request.
    #
    # A deleted user and a disabled one give the same 401, on purpose: the
    # client's situation is identical, and distinguishing them would leak
    # whether an account exists.
    if user is None or not user.is_active:
        raise _UNAUTH
    return user


def require_roles(*roles: UserRole) -> Callable[..., Coroutine[Any, Any, User]]:
    """Build a dependency that admits only the given roles (or an admin).

    A *factory*: it returns a new dependency function with `roles` captured in
    a closure. That is what allows one guard to be configured per use site
    while still being a plain dependency FastAPI can resolve.

        _manage = require_roles(UserRole.DISPATCHER)   # bind once per module
        ...
        async def create_order(..., _=Depends(_manage)):
    """

    async def _guard(user: User = Depends(get_current_user)) -> User:
        # Note the nested Depends: this guard depends on get_current_user, so
        # FastAPI resolves the chain and authentication always happens before
        # authorization. A handler never has to declare both.
        if user.role == UserRole.ADMIN:
            return user  # admin can do everything

        # The ADMIN short-circuit above is the entire role ladder, expressed
        # once. It means require_roles(DISPATCHER) reads as "dispatcher or
        # admin", so no endpoint has to spell out both — and nobody can forget
        # the second argument and accidentally lock admins out.
        if user.role not in roles:
            raise _FORBIDDEN
        return user

    return _guard


# Convenience guards
#
# Pre-built at import time so the common cases are shared objects rather than a
# fresh closure per module. Route modules that need a different combination
# still call require_roles directly — see the inline admin guards on the
# vehicle and depot delete endpoints.
require_dispatcher = require_roles(UserRole.DISPATCHER)
require_admin = require_roles(UserRole.ADMIN)
