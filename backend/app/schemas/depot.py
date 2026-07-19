from __future__ import annotations

from datetime import time

from pydantic import BaseModel, ConfigDict, Field


class DepotBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    address: str | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    operating_start: time = time(8, 0)
    operating_end: time = time(20, 0)


class DepotCreate(DepotBase):
    pass


class DepotUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    address: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    operating_start: time | None = None
    operating_end: time | None = None


class DepotOut(DepotBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
