from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import FinanceSettings


def hash_finance_pin(pin: str) -> str:
    p = str(pin or "").strip()
    raw = f"nikcrm_finance_pin_v1:{p}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def is_valid_finance_pin(pin: str) -> bool:
    p = str(pin or "").strip()
    return len(p) == 6 and p.isdigit()


async def get_finance_settings(session: AsyncSession) -> FinanceSettings:
    row = (await session.execute(select(FinanceSettings).order_by(FinanceSettings.id.asc()).limit(1))).scalars().first()
    if row is None:
        row = FinanceSettings(
            id=1,
            pin_hash=hash_finance_pin("000000"),
            updated_by_user_id=None,
        )
        session.add(row)
        await session.flush()
    return row


async def verify_finance_pin(*, session: AsyncSession, pin: str) -> bool:
    row = await get_finance_settings(session)
    return str(row.pin_hash or "") == hash_finance_pin(pin)


async def set_finance_pin(*, session: AsyncSession, new_pin: str, updated_by_user_id: int | None) -> FinanceSettings:
    if not is_valid_finance_pin(new_pin):
        raise ValueError("invalid_pin")
    row = await get_finance_settings(session)
    row.pin_hash = hash_finance_pin(new_pin)
    row.updated_by_user_id = int(updated_by_user_id) if updated_by_user_id is not None else None
    session.add(row)
    await session.flush()
    return row


async def reset_finance_pin(*, session: AsyncSession, updated_by_user_id: int | None) -> FinanceSettings:
    return await set_finance_pin(session=session, new_pin="000000", updated_by_user_id=updated_by_user_id)


async def get_cash_balance(*, session: AsyncSession) -> Decimal:
    from decimal import Decimal
    row = await get_finance_settings(session)
    return Decimal(str(row.cash_balance or 0))


async def set_cash_balance(
    *,
    session: AsyncSession,
    value: Decimal,
    updated_by_user_id: int | None = None,
) -> FinanceSettings:
    from decimal import Decimal
    v = Decimal(str(value or 0))
    if v < 0:
        raise ValueError("invalid_amount")
    row = await get_finance_settings(session)
    row.cash_balance = v
    if updated_by_user_id is not None:
        row.updated_by_user_id = int(updated_by_user_id)
    session.add(row)
    await session.flush()
    return row
