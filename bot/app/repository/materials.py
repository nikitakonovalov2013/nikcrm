from __future__ import annotations

from sqlalchemy import select, exists, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import Material, material_master_access
from shared.permissions import UserRoleFlags


class MaterialsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _base_query():
        return select(Material).where(Material.is_active == True)

    @staticmethod
    def _master_access_filter(*, user_id: int):
        has_any_acl = exists(select(1).where(material_master_access.c.material_id == Material.id))
        has_user_acl = exists(
            select(1).where(
                and_(
                    material_master_access.c.material_id == Material.id,
                    material_master_access.c.user_id == user_id,
                )
            )
        )
        return or_(~has_any_acl, has_user_acl)

    async def list_all(self) -> list[Material]:
        res = await self.session.execute(self._base_query().order_by(Material.name))
        return list(res.scalars().all())

    async def list_for_stocks_view(self, *, r: UserRoleFlags, user_id: int | None) -> list[Material]:
        if r.is_admin or r.is_manager:
            return await self.list_all()
        if r.is_master and user_id is not None:
            res = await self.session.execute(
                self._base_query().where(self._master_access_filter(user_id=user_id))
                .order_by(Material.name)
            )
            return list(res.scalars().all())
        return []

    async def get_for_stocks_op(self, *, material_id: int, r: UserRoleFlags, user_id: int | None) -> Material | None:
        q = self._base_query().where(Material.id == material_id)
        if r.is_admin or r.is_manager:
            res = await self.session.execute(q)
            return res.scalar_one_or_none()
        if r.is_master and user_id is not None:
            res = await self.session.execute(q.where(self._master_access_filter(user_id=user_id)))
            return res.scalar_one_or_none()
        return None

    async def get_by_id(self, material_id: int) -> Material | None:
        res = await self.session.execute(self._base_query().where(Material.id == material_id))
        return res.scalar_one_or_none()

    async def list_accessible_for_master(self, user_id: int) -> list[Material]:
        res = await self.session.execute(
            self._base_query().where(self._master_access_filter(user_id=user_id))
            .order_by(Material.name)
        )
        return list(res.scalars().all())

    async def master_can_access(self, *, material_id: int, user_id: int) -> bool:
        has_any_acl = exists(select(1).where(material_master_access.c.material_id == material_id))
        has_user_acl = exists(
            select(1).where(
                and_(
                    material_master_access.c.material_id == material_id,
                    material_master_access.c.user_id == user_id,
                )
            )
        )
        res = await self.session.execute(select(or_(~has_any_acl, has_user_acl)))
        return bool(res.scalar_one())
