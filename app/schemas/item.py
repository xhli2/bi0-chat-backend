from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import TimestampModel


class ItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None


class ItemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class ItemOut(TimestampModel):
    id: int
    title: str
    description: str | None = None
    owner_id: int
    deleted_at: datetime | None = None
