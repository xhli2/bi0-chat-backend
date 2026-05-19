from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field


class MessageResponse(BaseModel):
    message: str


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)
    sort: str = Field(default="id")
    q: str | None = None
    filters: str | None = None


T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    page: int
    size: int
    total: int
    items: list[T]


class TimestampModel(BaseModel):
    created_at: datetime
    updated_at: datetime
