"""Authentication & authorization dependencies."""
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

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/login", auto_error=False)

_UNAUTH = APIError("NOT_AUTHENTICATED", "Authentication required", status_code=401)
_FORBIDDEN = APIError("FORBIDDEN", "You do not have permission to perform this action", status_code=403)


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not token:
        raise _UNAUTH
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise APIError("INVALID_TOKEN", "Could not validate credentials", status_code=401) from exc

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise _UNAUTH
    return user


def require_roles(*roles: UserRole) -> Callable[..., Coroutine[Any, Any, User]]:
    async def _guard(user: User = Depends(get_current_user)) -> User:
        if user.role == UserRole.ADMIN:
            return user  # admin can do everything
        if user.role not in roles:
            raise _FORBIDDEN
        return user

    return _guard


# Convenience guards
require_dispatcher = require_roles(UserRole.DISPATCHER)
require_admin = require_roles(UserRole.ADMIN)
