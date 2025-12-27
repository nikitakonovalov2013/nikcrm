from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import Material


class MaterialsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> list[Material]:
        res = await self.session.execute(select(Material).order_by(Material.name))
        return list(res.scalars().all())

    async def get_by_id(self, material_id: int) -> Material | None:
        res = await self.session.execute(select(Material).where(Material.id == material_id))
        return res.scalar_one_or_none()
