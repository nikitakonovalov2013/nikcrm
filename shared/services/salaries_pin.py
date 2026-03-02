from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import SalarySettings
from datetime import date


def hash_salary_pin(pin: str) -> str:
    p = str(pin or "").strip()
    raw = f"nikcrm_salary_pin_v1:{p}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def is_valid_salary_pin(pin: str) -> bool:
    p = str(pin or "").strip()
    return len(p) == 6 and p.isdigit()


async def get_salary_settings(session: AsyncSession) -> SalarySettings:
    row = (await session.execute(select(SalarySettings).order_by(SalarySettings.id.asc()).limit(1))).scalars().first()
    if row is None:
        row = SalarySettings(
            id=1,
            pin_hash=hash_salary_pin("000000"),
            balance_cutoff_date=date(2026, 3, 1),
            updated_by_user_id=None,
        )
        session.add(row)
        await session.flush()
    return row


async def verify_salary_pin(*, session: AsyncSession, pin: str) -> bool:
    row = await get_salary_settings(session)
    return str(row.pin_hash or "") == hash_salary_pin(pin)


async def set_salary_pin(*, session: AsyncSession, new_pin: str, updated_by_user_id: int | None) -> SalarySettings:
    if not is_valid_salary_pin(new_pin):
        raise ValueError("invalid_pin")
    row = await get_salary_settings(session)
    row.pin_hash = hash_salary_pin(new_pin)
    row.updated_by_user_id = int(updated_by_user_id) if updated_by_user_id is not None else None
    session.add(row)
    await session.flush()
    return row


async def reset_salary_pin(*, session: AsyncSession, updated_by_user_id: int | None) -> SalarySettings:
    return await set_salary_pin(session=session, new_pin="000000", updated_by_user_id=updated_by_user_id)
