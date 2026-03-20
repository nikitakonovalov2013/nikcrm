"""Shared warehouse valuation helper used by both the bot and the web app."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import Material

RUB_PER_KG = Decimal("530")


async def get_warehouse_value_rub(*, session: AsyncSession) -> int:
    """Return current warehouse value in rubles (total active stock kg × 530)."""
    rows = (
        await session.execute(
            select(Material.current_stock).where(Material.is_active == True)
        )
    ).all()
    total_kg = sum((Decimal(str(row[0] or 0)) for row in rows), Decimal(0))
    return int((total_kg * RUB_PER_KG).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
