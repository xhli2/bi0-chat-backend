from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import ApiError
from app.db.session import get_db_session
from app.models.user import User
from app.repositories.items import ItemRepository
from app.schemas.common import MessageResponse, PaginatedResponse, PaginationParams
from app.schemas.item import ItemCreate, ItemOut, ItemUpdate

router = APIRouter(prefix="/items", tags=["items"])


def _to_item_out(item) -> ItemOut:
    return ItemOut(
        id=item.id,
        title=item.title,
        description=item.description,
        owner_id=item.owner_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
        deleted_at=item.deleted_at,
    )


@router.post("", response_model=ItemOut)
async def create_item(
    payload: ItemCreate,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ItemOut:
    repo = ItemRepository(session)
    item = await repo.create(owner_id=user.id, title=payload.title, description=payload.description)
    return _to_item_out(item)


@router.get("", response_model=PaginatedResponse[ItemOut])
async def list_items(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    sort: str = Query("id"),
    q: str | None = Query(None),
    filters: str | None = Query(None, description="JSON string filters, e.g. {\"title\":\"foo\"}"),
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> PaginatedResponse[ItemOut]:
    repo = ItemRepository(session)
    params = PaginationParams(page=page, size=size, sort=sort, q=q, filters=filters)
    items, total = await repo.list(owner_id=user.id, params=params)
    return PaginatedResponse[ItemOut](
        page=page,
        size=size,
        total=total,
        items=[_to_item_out(item) for item in items],
    )


@router.patch("/{item_id}", response_model=ItemOut)
async def update_item(
    item_id: int,
    payload: ItemUpdate,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ItemOut:
    repo = ItemRepository(session)
    item = await repo.get(owner_id=user.id, item_id=item_id)
    if item is None:
        raise ApiError(status_code=404, code="ITEM_NOT_FOUND", detail="Item not found.")
    updated = await repo.update(item=item, title=payload.title, description=payload.description)
    return _to_item_out(updated)


@router.delete("/{item_id}", response_model=MessageResponse)
async def delete_item(
    item_id: int,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> MessageResponse:
    repo = ItemRepository(session)
    item = await repo.get(owner_id=user.id, item_id=item_id)
    if item is None:
        raise ApiError(status_code=404, code="ITEM_NOT_FOUND", detail="Item not found.")
    await repo.soft_delete(item)
    return MessageResponse(message="deleted")


@router.post("/{item_id}/restore", response_model=ItemOut)
async def restore_item(
    item_id: int,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ItemOut:
    repo = ItemRepository(session)
    item = await repo.get(owner_id=user.id, item_id=item_id, include_deleted=True)
    if item is None:
        raise ApiError(status_code=404, code="ITEM_NOT_FOUND", detail="Item not found.")
    restored = await repo.restore(item)
    return _to_item_out(restored)
