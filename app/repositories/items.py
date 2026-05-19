import json

from datetime import datetime, timezone

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.item import Item
from app.schemas.common import PaginationParams


class ItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _base_query(self, owner_id: int, include_deleted: bool = False) -> Select[tuple[Item]]:
        stmt = select(Item).where(Item.owner_id == owner_id)
        if not include_deleted:
            stmt = stmt.where(Item.deleted_at.is_(None))
        return stmt

    async def create(self, owner_id: int, title: str, description: str | None) -> Item:
        item = Item(owner_id=owner_id, title=title, description=description)
        self.session.add(item)
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def list(self, owner_id: int, params: PaginationParams) -> tuple[list[Item], int]:
        stmt = self._base_query(owner_id=owner_id)
        if params.q:
            stmt = stmt.where(Item.title.ilike(f"%{params.q}%"))
        if params.filters:
            parsed = self._parse_filters(params.filters)
            if "title" in parsed and parsed["title"]:
                stmt = stmt.where(Item.title.ilike(f"%{parsed['title']}%"))

        if params.sort in {"id", "-id", "title", "-title", "created_at", "-created_at"}:
            desc = params.sort.startswith("-")
            field = params.sort.lstrip("-")
            col = getattr(Item, field)
            stmt = stmt.order_by(col.desc() if desc else col.asc())

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()

        offset = (params.page - 1) * params.size
        rows = await self.session.execute(stmt.offset(offset).limit(params.size))
        return list(rows.scalars().all()), total

    def _parse_filters(self, raw_filters: str) -> dict[str, str]:
        try:
            parsed = json.loads(raw_filters)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        normalized: dict[str, str] = {}
        for key, value in parsed.items():
            if isinstance(key, str) and isinstance(value, str):
                normalized[key] = value
        return normalized

    async def get(self, owner_id: int, item_id: int, include_deleted: bool = False) -> Item | None:
        stmt = self._base_query(owner_id=owner_id, include_deleted=include_deleted).where(Item.id == item_id)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def update(self, item: Item, title: str | None, description: str | None) -> Item:
        if title is not None:
            item.title = title
        if description is not None:
            item.description = description
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def soft_delete(self, item: Item) -> Item:
        item.deleted_at = datetime.now(timezone.utc)
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def restore(self, item: Item) -> Item:
        item.deleted_at = None
        await self.session.commit()
        await self.session.refresh(item)
        return item
