"""User administration. One endpoint, admin only."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.auth import UserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
async def list_users(db: AsyncSession = Depends(get_db), _=Depends(require_roles(UserRole.ADMIN))):
    """Every user account. **Admin only** — this is the account list.

    Guards inline with require_roles(ADMIN) rather than binding a module-level
    alias, since there is only one endpoint here to guard.

    Note there is no pagination. Fine for a demo-scale user table, and the
    first thing to add if the account list grows — unlike /orders, which is
    paginated because it is expected to be large.
    """
    # Ordered by id so the list is stable across calls; an unordered SELECT may
    # return rows in any order, which makes a UI list appear to shuffle.
    return list((await db.execute(select(User).order_by(User.id))).scalars().all())
