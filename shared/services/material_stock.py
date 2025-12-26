from __future__ import annotations
from decimal import Decimal
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from shared.models import Material, MaterialConsumption, MaterialSupply


DECIMAL_ZERO = Decimal("0")


async def recalculate_material_stock(session: AsyncSession, material_id: int) -> None:
    res_sup = await session.execute(
        select(func.coalesce(func.sum(MaterialSupply.amount), 0)).where(MaterialSupply.material_id == material_id)
    )
    total_sup = res_sup.scalar_one()
    res_cons = await session.execute(
        select(func.coalesce(func.sum(MaterialConsumption.amount), 0)).where(
            MaterialConsumption.material_id == material_id
        )
    )
    total_cons = res_cons.scalar_one()
    res_mat = await session.execute(select(Material).where(Material.id == material_id))
    mat = res_mat.scalar_one()
    # Material.current_stock is Numeric(16,3); rely on DB to keep scale. Cast to Decimal for arithmetic.
    stock = (Decimal(total_sup) if total_sup is not None else DECIMAL_ZERO) - (
        Decimal(total_cons) if total_cons is not None else DECIMAL_ZERO
    )
    mat.current_stock = stock
    await session.flush()


async def update_stock_on_new_consumption(session: AsyncSession, consumption: MaterialConsumption) -> None:
    res_mat = await session.execute(select(Material).where(Material.id == consumption.material_id))
    mat = res_mat.scalar_one()
    mat.current_stock = (Decimal(mat.current_stock or 0) - Decimal(consumption.amount))
    await session.flush()


async def update_stock_on_new_supply(session: AsyncSession, supply: MaterialSupply) -> None:
    res_mat = await session.execute(select(Material).where(Material.id == supply.material_id))
    mat = res_mat.scalar_one()
    mat.current_stock = (Decimal(mat.current_stock or 0) + Decimal(supply.amount))
    await session.flush()
