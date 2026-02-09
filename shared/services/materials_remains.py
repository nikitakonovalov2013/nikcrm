from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import Material


@dataclass(frozen=True)
class SetRemainsResult:
    material_id: int
    old_remains: Decimal
    new_remains: Decimal
    delta: Decimal


async def set_material_remains(
    *,
    session: AsyncSession,
    material_id: int,
    new_remains: Decimal,
) -> SetRemainsResult:
    """Set material.current_stock to an absolute value (manual adjustment).

    This does NOT create supply/consumption records; caller must ensure audit/logging.
    """

    if new_remains is None:
        raise ValueError("new_remains_required")

    try:
        new_remains_d = Decimal(new_remains)
    except Exception as e:
        raise ValueError("invalid_new_remains") from e

    if new_remains_d < Decimal("0"):
        raise ValueError("new_remains_negative")

    res = await session.execute(select(Material).where(Material.id == int(material_id)))
    m = res.scalar_one_or_none()
    if not m:
        raise ValueError("material_not_found")

    old = Decimal(getattr(m, "current_stock", 0) or 0)
    m.current_stock = new_remains_d
    await session.flush()

    return SetRemainsResult(
        material_id=int(material_id),
        old_remains=old,
        new_remains=new_remains_d,
        delta=(new_remains_d - old),
    )
