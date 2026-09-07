"""Depot endpoints. Standard CRUD — see orders.py for the annotated pattern."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user, require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.schemas.common import Message
from app.schemas.depot import DepotCreate, DepotOut, DepotUpdate
from app.services import depot_service

router = APIRouter(prefix="/depots", tags=["depots"])
_manage = require_roles(UserRole.DISPATCHER)


@router.get("", response_model=list[DepotOut])
async def list_depots(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    """Every depot. Unpaginated and unfiltered — there are only ever a handful."""
    return await depot_service.list_depots(db)


@router.get("/{depot_id}", response_model=DepotOut)
async def get_depot(depot_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    return await depot_service.get_depot(db, depot_id)


@router.post("", response_model=DepotOut, status_code=201)
async def create_depot(data: DepotCreate, db: AsyncSession = Depends(get_db), _=Depends(_manage)):
    """Create a depot. The service derives the PostGIS point from lat/lng."""
    return await depot_service.create_depot(db, data)


@router.patch("/{depot_id}", response_model=DepotOut)
async def update_depot(
    depot_id: int, data: DepotUpdate, db: AsyncSession = Depends(get_db), _=Depends(_manage)
):
    """Partial update. If either coordinate changes the service rebuilds the
    geography point, so the two representations cannot drift apart."""
    return await depot_service.update_depot(db, depot_id, data)


@router.delete("/{depot_id}", response_model=Message)
async def delete_depot(depot_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_roles(UserRole.ADMIN))):
    """Delete a depot. **Admin only — the most destructive call in the API.**

    Every foreign key pointing at a depot is ON DELETE CASCADE, so this removes
    the depot's vehicles, its orders *and* its routes. Nothing warns further and
    there is no soft-delete: that is precisely why it sits behind the highest
    role, guarded inline so the requirement is visible at the call site.
    """
    await depot_service.delete_depot(db, depot_id)
    return Message(message="Depot deleted")
